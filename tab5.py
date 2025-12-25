import streamlit as st
import pandas as pd
import json, io, zipfile, time, subprocess, tempfile, os
from datetime import datetime
from collections import OrderedDict
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

def tab5_content():
    st.header("多语言合并与编辑工作台")
    st.info("1. 上传多组 txt/ini 或单个 xlsx。\n2. 合并表格，支持实时编辑。\n3. 实时统计语言的合格/过短/过长情况。\n4. 导出 xlsx 或按语言导出 txt/ini（可打包）。")

    # ---------------------------
    # 上传模式选择
    # ---------------------------
    mode = st.radio("选择上传方式", options=["上传多语言 txt/ini 并合并", "上传单个 XLSX（直接读取）"])

    uploaded_files = []
    uploaded_xlsx = None

    if mode.startswith("上传多语言"):
        uploaded_files = st.file_uploader("上传多语言文件 (.txt/.ini)，可多选", type=['txt','ini'], accept_multiple_files=True)
    else:
        uploaded_xlsx = st.file_uploader("上传单个 Excel (.xlsx/.xls)", type=['xlsx','xls'])

    # ---------------------------
    # 标签（状态）配置（与 Tab1 保持一致）
    # ---------------------------
    st.subheader("标签配置（与 Tab1 一致）")
    st.info("设置标签区间与颜色；默认顺序首位视为基础/首选“合格”。\n（注意：脚本将第一个上传的语言视为基础语言，支持更改；基础语言不做标签统计。）")

    default_statuses = [
        {"name": "合格", "min": -0.4, "max": 2, "color": "#00A000"},
        {"name": "过短", "min": -99999, "max": -0.4, "color": "#0071A6"},
        {"name": "过长", "min": 2, "max": 99999, "color": "#A60000"}
    ]
    status_count = st.number_input("标签数量", min_value=1, max_value=10, value=len(default_statuses), step=1, key="tab5_status_count")
    custom_statuses = []
    cols = st.columns([3,2,2,2])
    for i in range(status_count):
        default = default_statuses[i] if i < len(default_statuses) else {"name": f"标签{i+1}", "min": -99999, "max": 99999, "color": "#FFFFFF"}
        c1, c2, c3, c4 = st.columns([3,2,2,2])
        with c1:
            name = st.text_input(f"标签{i+1} 名称", value=default["name"], key=f"tname_{i}")
        with c2:
            min_val = st.number_input(f"标签{i+1} 最小值", value=float(default["min"]), key=f"tmin_{i}")
        with c3:
            max_val = st.number_input(f"标签{i+1} 最大值", value=float(default["max"]), key=f"tmax_{i}")
        with c4:
            color = st.color_picker(f"标签{i+1} 颜色", value=default["color"], key=f"tcol_{i}")
        custom_statuses.append({"name": name, "min": min_val, "max": max_val, "color": color})

    # ---------------------------
    # 解析单个 txt/ini (BytesIO) -> dict
    # ---------------------------
    def parse_txt_bytes(file):
        d = {}
        try:
            text = file.getvalue().decode('utf-8')
        except:
            text = file.getvalue().decode('latin-1')
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(';') or line.startswith('#'):
                continue
            if '=' in line:
                k,v = line.split('=',1)
                d[k.strip()] = v.strip()
        return d

    # ---------------------------
    # 合并多文件（或读取单xlsx） -> DataFrame
    # ---------------------------
    def build_dataframe_from_files(files, custom_names):
        language_data = OrderedDict()
        language_names = []
        for idx, f in enumerate(files):
            name = custom_names[idx]
            language_names.append(name)
            language_data[name] = parse_txt_bytes(f)
        base = language_names[0]
        base_keys = list(language_data[base].keys())
        all_keys = set()
        for d in language_data.values():
            all_keys.update(d.keys())
        result = OrderedDict()
        result['编号'] = []
        for lang in language_names:
            result[lang] = []
        processed = set()
        for k in base_keys:
            result['编号'].append(k)
            for lang in language_names:
                result[lang].append(language_data[lang].get(k, ''))
            processed.add(k)
        for k in sorted(all_keys - processed):
            result['编号'].append(k)
            for lang in language_names:
                result[lang].append(language_data[lang].get(k, ''))
        return pd.DataFrame(result), language_names

    def build_dataframe_from_xlsx(uploaded):
        # read as pandas
        df = pd.read_excel(uploaded, engine='openpyxl')
        # ensure '编号' exists
        if '编号' not in df.columns:
            st.error("Excel 缺少 '编号' 列，请检查。")
            return None, []
        # reorder columns: 编号 first
        cols = list(df.columns)
        cols = [c for c in cols if c != '编号']
        ordered = ['编号'] + cols
        df = df[ordered]
        return df, cols

    # ---------------------------
    # 当使用多 txt/ini 模式，要求用户输入列名
    # ---------------------------
    custom_names = []
    if mode.startswith("上传多语言") and uploaded_files:
        st.subheader("为每个上传的文件自定义列名")
        for f in uploaded_files:
            default = os.path.splitext(f.name)[0]
            n = st.text_input(f"{f.name} 命名", value=default, key=f"colname_{f.name}")
            custom_names.append(n)

    # ---------------------------
    # 生成初始 DataFrame（来自上传）
    # ---------------------------
    df = None
    language_cols = []
    if mode.startswith("上传多语言") and uploaded_files and custom_names and all(custom_names):
        with st.spinner("合并上传的 txt/ini 文件为表格..."):
            df, language_cols = build_dataframe_from_files(uploaded_files, custom_names)
    elif mode.startswith("上传单个") and uploaded_xlsx:
        with st.spinner("读取 Excel 文件..."):
            df, language_cols = build_dataframe_from_xlsx(uploaded_xlsx)
            # language_cols is list of non-'编号' columns

    # 没有表格时提示
    if df is None:
        st.info("等待上传文件或填写列名后生成表格...")
        st.stop()

    # ---------------------------
    # 基础语言选择（默认第一列）
    # ---------------------------
    st.subheader("基础语言（用于比值基准，基础语言不做标签统计）")
    all_langs = [c for c in df.columns if c != '编号']
    base_lang = st.selectbox("选择基础语言（基准列）", options=all_langs, index=0)

    # ---------------------------
    # 计算隐式标签（每个非基础语言单元格）
    # 将为每个非基础语言生成一个隐藏列 <lang>__tag 保存标签名
    # ---------------------------
    def compute_cell_tags(df_df, statuses, base_col):
        df_calc = df_df.copy()
        # ensure strings
        for col in df_calc.columns:
            df_calc[col] = df_calc[col].astype(object).fillna('')
        tag_map = {}  # (row_idx, col) -> tag
        # iterate rows
        for i, row in df_calc.iterrows():
            base_val = str(row[base_col]) if pd.notna(row[base_col]) else ''
            base_len = len(base_val)
            for col in df_calc.columns:
                if col == '编号' or col == base_col:
                    continue
                val = str(row[col]) if pd.notna(row[col]) else ''
                val_len = len(val)
                if base_len == 0:
                    tag = None
                else:
                    ratio = (val_len - base_len) / base_len
                    # find matching status by order (first match)
                    tag = None
                    for s in statuses:
                        s_min = float("-inf") if s.get("min") is None else s["min"]
                        s_max = float("inf") if s.get("max") is None else s["max"]
                        if s_min <= ratio <= s_max:
                            tag = s["name"]
                            break
                tag_map[(i, col)] = tag
        return tag_map

    # initial compute
    tag_map = compute_cell_tags(df, custom_statuses, base_lang)

    # Build DataFrame that includes hidden tag columns
    df_display = df.copy()
    for col in df.columns:
        if col == '编号' or col == base_lang:
            continue
        tag_col = f"{col}__tag"
        df_display[tag_col] = [tag_map.get((i, col)) for i in range(len(df_display))]

    # ---------------------------
    # 构建 AgGrid 并支持编辑：当用户编辑时，从 response 获取新数据，重新计算 tag_map 并刷新
    # ---------------------------
    gb = GridOptionsBuilder.from_dataframe(df_display)
    # make non-'编号' columns editable
    for c in df.columns:
        if c == '编号':
            gb.configure_column(c, resizable=True, filter=True, sortable=True, editable=True, wrapText=True, autoHeight=True)
        else:
            gb.configure_column(c, resizable=True, filter=True, sortable=True, editable=True, wrapText=True, autoHeight=True)
    # hide tag cols
    for c in df.columns:
        if c == '编号' or c == base_lang:
            continue
        gb.configure_column(f"{c}__tag", hide=True)
    gb.configure_default_column(resizable=True, filter=True, sortable=True, editable=True, wrapText=True, autoHeight=True)
    gb.configure_selection("multiple", use_checkbox=True)

    # cellStyle JS: read tag from hidden column and map to color
    status_colors = {s["name"]: s["color"] for s in custom_statuses}
    js_status_colors = json.dumps(status_colors)

    # For each visible non-base column, set a cellStyle JsCode referencing its tag column
    for col in df.columns:
        if col == '编号' or col == base_lang:
            continue
        tag_col = f"{col}__tag"
        js = JsCode(f"""
        function(params) {{
            const colors = {js_status_colors};
            const tag = params.data['{tag_col}'];
            if (tag && colors[tag]) {{
                return {{backgroundColor: colors[tag]}};
            }} else {{
                return {{}};
            }}
        }}
        """)
        gb.configure_column(col, cellStyle=js, tooltipField=col, wrapText=True, autoHeight=True)

    # For base column and 编号 column add tooltip
    gb.configure_column('编号', tooltipField='编号')
    gb.configure_column(base_lang, tooltipField=base_lang)

    grid_options = gb.build()

    # Display editable AgGrid, capture edits
    grid_response = AgGrid(
        df_display,
        gridOptions=grid_options,
        height=500,
        fit_columns_on_grid_load=True,
        enable_enterprise_modules=False,
        allow_unsafe_jscode=True,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED
    )

    # When user edits, grid_response['data'] contains updated rows
    updated = pd.DataFrame(grid_response['data'])

    # If there were tag columns, drop them and rebuild core df
    core_cols = ['编号'] + [c for c in df.columns if c != '编号']
    # ensure updated has those columns (it will include tag cols)
    # Build new core df
    new_df = pd.DataFrame({c: updated[c] for c in core_cols})

    # Recompute tags based on edited content
    tag_map = compute_cell_tags(new_df, custom_statuses, base_lang)

    # Rebuild updated display df (with updated hidden tag cols)
    df_display = new_df.copy()
    for col in new_df.columns:
        if col == '编号' or col == base_lang:
            continue
        df_display[f"{col}__tag"] = [tag_map.get((i, col)) for i in range(len(df_display))]

    # Show statistics above or to the side
    st.subheader("每语言统计（基础语言不做标签统计）")
    stats_records = []
    total_valid_base = new_df['编号'].notna().sum()  # used for counts baseline if needed

    for lang in [c for c in new_df.columns if c != '编号']:
        if lang == base_lang:
            valid = new_df[lang].astype(str).str.strip().apply(bool).sum()
            stats_records.append({
                "语言": lang,
                "有效字段数": int(valid),
                "合格": "",
                "过短": "",
                "过长": ""
            })
        else:
            col_series = new_df[lang].astype(str).fillna('')
            valid = col_series.str.strip().apply(bool).sum()
            # compute counts by status
            counts = {s["name"]: 0 for s in custom_statuses}
            for i, val in col_series.items():
                tag = tag_map.get((i, lang))
                if tag:
                    counts[tag] = counts.get(tag, 0) + 1
            # build percentages relative to number of rows that have a base (we consider rows where base exists)
            base_exists_mask = new_df[base_lang].astype(str).str.strip().apply(bool)
            total_for_pct = base_exists_mask.sum()
            # prepare record
            rec = {
                "语言": lang,
                "有效字段数": int(valid)
            }
            for s in custom_statuses:
                cnt = counts.get(s["name"], 0)
                pct = (cnt / total_for_pct * 100) if total_for_pct else 0
                rec[s["name"]] = f"{cnt} ({pct:.2f}%)"
            stats_records.append(rec)

    stats_df = pd.DataFrame(stats_records)
    # show nicely
    st.dataframe(stats_df)

    # ---------------------------
    # 导出选项
    # ---------------------------
    st.subheader("导出选项")
    col1, col2 = st.columns([1,1])
    with col1:
        if st.button("导出当前表格为 XLSX"):
            buffer = io.BytesIO()
            # export new_df (编号 + languages)
            export_df = new_df.copy()
            export_df.to_excel(buffer, index=False, engine='openpyxl')
            buffer.seek(0)
            st.download_button("Download XLSX", data=buffer.getvalue(), file_name=f"merged_{int(time.time())}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with col2:
        out_fmt = st.selectbox("按语言导出格式", options=["txt","ini"], index=0)
        zip_now = st.button("导出所有语言为单独文件（打包 ZIP ）")
        if zip_now:
            mem_zip = io.BytesIO()
            with zipfile.ZipFile(mem_zip, "w") as zf:
                for lang in [c for c in new_df.columns if c != '编号']:
                    content_lines = []
                    for _, row in new_df.iterrows():
                        idv = str(row['编号']).strip()
                        if not idv:
                            continue
                        cell = "" if pd.isna(row[lang]) else str(row[lang])
                        content_lines.append(f"{idv}={cell}")
                    file_bytes = "\n".join(content_lines).encode('utf-8')
                    zf.writestr(f"{lang}.{out_fmt}", file_bytes)
            mem_zip.seek(0)
            st.download_button("下载 ZIP（所有语言）", data=mem_zip.getvalue(), file_name=f"languages_{int(time.time())}.zip", mime="application/zip")

    st.success("Tab5 执行完毕 — 编辑后表格会自动生效并更新统计。")
