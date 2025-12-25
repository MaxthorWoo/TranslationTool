import streamlit as st
import pandas as pd
import json, io, zipfile, time, subprocess, tempfile, os
from datetime import datetime
from collections import OrderedDict
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

def tab1_content():
    st.header("翻译长度检查")
    st.info("上传原文文件和翻译文件后，工具会计算每个字段的长度比值，并标记为标签。可自定义标签，也可使用默认过短/合格/过长标签。")

    # 上传原文文件和翻译文件
    original_file = st.file_uploader("上传原文文件 (.txt)", type="txt")
    translation_file = st.file_uploader("上传翻译文件 (.txt)", type="txt")
    iteration_file = st.file_uploader("上传迭代文件 (.txt, 可选)", type="txt")

    def parse_txt(file):
        result = {}
        # 使用 utf-8-sig 自动移除 BOM，并用 errors='replace' 防止奇怪编码报错
        text = file.getvalue().decode("utf-8-sig", errors="replace")
        for line in text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key:  # 忽略空 key
                    result[key] = value
        return result

    # ---- 标签自定义配置 ----
    st.subheader("自定义标签设置（可选）")
    st.info("如果不修改，默认使用：合格 / 过短 / 过长 标签。")

    default_statuses = [
        {"name": "合格", "min": -0.4, "max": 2, "color": "#00A000"},
        {"name": "过短", "min": -99999, "max": -0.4, "color": "#0071A6"},
        {"name": "过长", "min": 2, "max": 99999, "color": "#A60000"}
    ]

    status_count = st.number_input("标签数量", min_value=1, max_value=10, value=len(default_statuses), step=1, key="tab1_status_count")
    custom_statuses = []

    st.write("标签配置")
    for i in range(status_count):
        default = default_statuses[i] if i < len(default_statuses) else {"name": f"标签{i+1}", "min": -99999, "max": 99999, "color": "#FFFFFF"}
        col1, col2, col3, col4 = st.columns([3,2,2,2])
        with col1:
            name = st.text_input(f"标签{i+1} 名称", value=default["name"])
        with col2:
            min_val = st.number_input(f"标签{i+1} 最小值", value=float(default["min"]))
        with col3:
            max_val = st.number_input(f"标签{i+1} 最大值", value=float(default["max"]))
        with col4:
            color = st.color_picker(f"标签{i+1} 颜色", value=default["color"])
        custom_statuses.append({"name": name.strip(), "min": min_val, "max": max_val, "color": color})

    # ---- 可迭代标签选择 ----
    st.subheader("选择可迭代标签(默认合格)")
    st.info("注意：系统会计算迭代文件中每个条目的标签，只有标签在可迭代列表中的条目才会被更新到翻译文件中。")
    
    iterable_labels = st.multiselect(
        "选择哪些标签可以被迭代",
        options=[s["name"] for s in custom_statuses],
        default=[custom_statuses[0]["name"]]  # 默认第一个标签（通常是"合格"）
    )

    # ----- 计算单个条目的标签函数 -----
    def calculate_single_status(orig_text, trans_text, statuses):
        orig_len = len(orig_text)
        trans_len = len(trans_text)

        if orig_len == 0:
            return "原文为空"

        ratio = (trans_len - orig_len) / orig_len
        ratio = round(ratio, 4)

        for s in statuses:
            s_min = float("-inf") if s.get("min") is None else s["min"]
            s_max = float("inf") if s.get("max") is None else s["max"]
            if s_min <= ratio <= s_max:
                return s["name"]
        
        return "未分类"

    # ----- 计算比值和标签的函数 -----
    def calculate_length_status(original_dict, translation_dict, statuses):
        data = []
        for key, orig_text in original_dict.items():
            trans_text = translation_dict.get(key, "")
            orig_len = len(orig_text)
            trans_len = len(trans_text)

            if orig_len == 0:
                ratio = None
                ratio_percent = ""
            else:
                ratio = (trans_len - orig_len) / orig_len
                ratio = round(ratio, 4)
                ratio_percent = f"{ratio*100:.2f}%"

            status = "原文为空" if orig_len == 0 else None

            if ratio is not None:
                for s in statuses:
                    s_min = float("-inf") if s.get("min") is None else s["min"]
                    s_max = float("inf") if s.get("max") is None else s["max"]
                    if s_min <= ratio <= s_max:
                        status = s["name"]
                        break
                if status is None:
                    status = "未分类"

            data.append({
                "编号": key,
                "原文": orig_text,
                "译文": trans_text,
                "原文长度": orig_len,
                "译文长度": trans_len,
                "比值": ratio,
                "比值(%)": ratio_percent,
                "标签": status
            })
        return pd.DataFrame(data)

    # ---- 统计信息函数 ----
    def compute_statistics(df, statuses, total_field="原文"):
        records = []
        total_valid = df[total_field].apply(lambda x: bool(x.strip())).sum()
        records.append({"类型": f"{total_field}有效字段数量", "数量": total_valid, "占比": ""})
        total_trans = df["译文"].apply(lambda x: bool(x.strip())).sum()
        records.append({"类型": "译文有效字段数量", "数量": total_trans, "占比": ""})
        for s in statuses:
            count = df[df["标签"] == s["name"]].shape[0]
            ratio = (count / total_valid * 100) if total_valid else 0
            records.append({"类型": s["name"], "数量": count, "占比": f"{ratio:.2f}%"})
        return pd.DataFrame(records)

    if original_file and translation_file:
        original_dict = parse_txt(original_file)
        translation_dict = parse_txt(translation_file)

        # 初始化迭代统计
        iteration_stats = {
            "total_in_iteration": 0,
            "matched_in_original": 0,
            "updated_translations": 0,
            "skipped_not_iterable": 0,
            "iteration_labels_distribution": {}
        }

        # ---- 迭代文件更新翻译字典 ----
        if iteration_file:
            iteration_dict = parse_txt(iteration_file)
            iteration_stats["total_in_iteration"] = len(iteration_dict)
            
            # 规范化可迭代标签列表
            iterable_labels_norm = [label.strip() for label in iterable_labels]
            
            # 用于跟踪哪些记录被更新了
            updated_keys = []
            
            # 首先分析迭代文件中每个条目的标签分布
            iteration_labels_count = {}
            
            for key, iteration_value in iteration_dict.items():
                if key in original_dict:
                    iteration_stats["matched_in_original"] += 1
                    
                    # 获取原文内容
                    original_text = original_dict[key]
                    
                    # 计算迭代条目的标签（基于迭代文件的译文和原文）
                    iteration_label = calculate_single_status(original_text, iteration_value, custom_statuses)
                    
                    # 记录标签分布
                    iteration_labels_count[iteration_label] = iteration_labels_count.get(iteration_label, 0) + 1
                    
                    # 检查迭代条目的标签是否在可迭代标签列表中
                    if iteration_label in iterable_labels_norm:
                        # 更新翻译字典
                        translation_dict[key] = iteration_value
                        iteration_stats["updated_translations"] += 1
                        updated_keys.append((key, iteration_label))
                    else:
                        iteration_stats["skipped_not_iterable"] += 1
                else:
                    # 迭代文件中的键在原文中不存在，跳过
                    pass
            
            iteration_stats["iteration_labels_distribution"] = iteration_labels_count
            
            # 显示迭代统计信息
            if iteration_stats["total_in_iteration"] > 0:
                st.success(f"迭代文件处理完成:")
                
                # 创建迭代分析表格
                iteration_analysis = []
                for label, count in iteration_labels_count.items():
                    percentage = (count / iteration_stats["matched_in_original"] * 100) if iteration_stats["matched_in_original"] > 0 else 0
                    iteration_analysis.append({
                        "标签": label,
                        "数量": count,
                        "占比": f"{percentage:.1f}%",
                        "是否可迭代": "是" if label in iterable_labels_norm else "否"
                    })
                
                iteration_df = pd.DataFrame(iteration_analysis)
                
                col1, col2 = st.columns(2)
                with col1:
                    st.info(f"""
                    - 迭代文件总条目数: {iteration_stats['total_in_iteration']}
                    - 匹配到原文的条目: {iteration_stats['matched_in_original']}
                    - 已更新的翻译: {iteration_stats['updated_translations']}
                    - 不符合标准的翻译: {iteration_stats['skipped_not_iterable']}
                    """)
                
                with col2:
                    st.dataframe(iteration_df)
                
                if updated_keys:
                    # 构建展示表格
                    display_rows = []
                    before_update_translation = translation_dict.copy()
                    updated_records = []

                    for key, iteration_value in iteration_dict.items():
                        if key in original_dict:
                            original_text = original_dict.get(key, "")
                            old_translation = before_update_translation.get(key, "")
                            # 计算迭代条目的标签（基于迭代文件的译文）
                            iteration_label = calculate_single_status(original_text, iteration_value, custom_statuses)

                            if iteration_label in iterable_labels_norm:
                                # 更新翻译字典（这是实际生效的更新）
                                translation_dict[key] = iteration_value

                                # 计算原比值与新比值（与 calculate_length_status 保持一致的定义）
                                orig_len = len(original_text)
                                old_trans_len = len(old_translation)
                                new_trans_len = len(iteration_value)

                                if orig_len == 0:
                                    original_ratio = None
                                    original_ratio_pct = ""
                                    new_ratio = None
                                    new_ratio_pct = ""
                                else:
                                    original_ratio = round((old_trans_len - orig_len) / orig_len, 4)
                                    original_ratio_pct = f"{original_ratio*100:.2f}%"
                                    new_ratio = round((new_trans_len - orig_len) / orig_len, 4)
                                    new_ratio_pct = f"{new_ratio*100:.2f}%"

                                # 计算新标签（基于新译文）
                                new_label = calculate_single_status(original_text, iteration_value, custom_statuses)

                                updated_records.append({
                                    "编号": key,
                                    "原文": original_text,
                                    "原译文": old_translation,
                                    "新译文": iteration_value,
                                    "原比值": original_ratio,
                                    "原比值(%)": original_ratio_pct,
                                    "新比值": new_ratio,
                                    "新比值(%)": new_ratio_pct,
                                    "新标签": new_label
                                })

                    # ---- 在迭代处理结束后：展示全部更新记录（若有） ----
                    if updated_records:
                        df_updated = pd.DataFrame(updated_records)
                        st.subheader("本次迭代更新明细")
                        # 直接展示完整表格（可滚动、可排序）
                        st.dataframe(df_updated)
                    else:
                        st.info("本次迭代没有更新任何条目（或无匹配可迭代标签）。")

        # ---- 重新计算最终 DataFrame ----
        df_result = calculate_length_status(original_dict, translation_dict, custom_statuses)

        # ---- 上传文件整体统计 ----
        st.subheader("字段统计信息（原文 vs 译文）")
        stats_df = compute_statistics(df_result, custom_statuses, total_field="原文")
        st.dataframe(
            stats_df.reset_index(drop=True)
                    .style
                    .set_properties(subset=["类型"], **{'text-align': 'left'})
                    .set_properties(subset=["数量","占比"], **{'text-align': 'center'})
        )

        # ---- AgGrid 显示 ----
        st.subheader("翻译长度检查结果")
        st.info("下表显示每个字段的原文、译文、长度及标签，可选择导出过短或过长字段。")

        gb = GridOptionsBuilder.from_dataframe(df_result)
        gb.configure_selection("multiple", use_checkbox=True)
        gb.configure_default_column(filter=True, sortable=True, resizable=True)
        status_colors = {s["name"]: s["color"] for s in custom_statuses}
        cellstyle_jscode = JsCode(f"""
        function(params) {{
            const colors = {json.dumps(status_colors)};
            if (colors[params.value]) {{
                return {{backgroundColor: colors[params.value]}};
            }} else {{
                return {{}};
            }}
        }}
        """)
        gb.configure_column("标签", cellStyle=cellstyle_jscode)
        for col in df_result.columns:
            gb.configure_column(col, tooltipField=col)
        grid_options = gb.build()
        AgGrid(df_result, gridOptions=grid_options, height=600, fit_columns_on_grid_load=True,
               enable_enterprise_modules=False, allow_unsafe_jscode=True)

        # ---- 导出功能 ----
        st.subheader("选择导出条件")
        st.info("选择要导出的字段标签，并可选择拆分导出文件。")
        export_checks = {}
        for s in custom_statuses:
            export_checks[s["name"]] = st.checkbox(f"{s['name']}字段", value=(s["name"] in ["过短","过长"]))
        export_df = df_result[df_result["标签"].isin([name for name, checked in export_checks.items() if checked])]

        if not export_df.empty:
            st.write(f"符合条件的字段数量: {len(export_df)}")
            split_lines = st.number_input("每个拆分文件行数（留空或 0 表示不拆分）", min_value=0, value=0, step=1, key="tab1_split_lines")

            if split_lines > 0:
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                    total_lines = len(export_df)
                    num_parts = (total_lines + split_lines - 1) // split_lines
                    st.write(f"将拆分成 {num_parts} 个文件，每个最多 {split_lines} 行。")
                    for i in range(num_parts):
                        part_df = export_df.iloc[i*split_lines:(i+1)*split_lines]
                        part_txt = "\n".join([f"{row['编号']}={row['原文']}" for idx,row in part_df.iterrows()])
                        part_name = f"筛选原文_part_{i+1}.txt"
                        zip_file.writestr(part_name, part_txt)
                zip_buffer.seek(0)
                st.download_button(label=f"下载拆分后的压缩包 ({num_parts} 个文件)",
                                   data=zip_buffer.getvalue(),
                                   file_name=f"筛选原文拆分_{int(time.time())}.zip",
                                   mime="application/zip")
            else:
                export_txt = "\n".join([f"{row['编号']}={row['原文']}" for idx,row in export_df.iterrows()])
                st.download_button(label="下载筛选结果 (.txt)",
                                   data=export_txt,
                                   file_name=f"筛选原文_{int(time.time())}.txt",
                                   mime="text/plain")

        # ---- 导出最新版翻译文件 ----
        final_translation_txt = "\n".join([f"{key}={value}" for key,value in translation_dict.items()])
        st.download_button(label="导出最新版的总翻译文件 (.txt)",
                           data=final_translation_txt,
                           file_name="最新翻译.txt",
                           mime="text/plain")