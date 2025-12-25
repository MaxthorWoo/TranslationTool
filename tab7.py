import streamlit as st
import pandas as pd
import json, io, zipfile, subprocess, tempfile, os, re
from datetime import datetime
from collections import OrderedDict
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode
from cozepy import Coze as coze, TokenAuth, WorkflowEventType, COZE_CN_BASE_URL
import time as t
import hashlib
from concurrent.futures import ThreadPoolExecutor

def tab7_content():
    if "workflow_results" not in st.session_state:
        st.session_state.workflow_results = []

    if "workflow_raw_events" not in st.session_state:
        st.session_state.workflow_raw_events = []

    if "has_workflow_result" not in st.session_state:
        st.session_state.has_workflow_result = False

    if "iteration_dict" not in st.session_state:
        st.session_state.iteration_dict = {}
    # 会话中持久化待翻译队列和最新译文，避免重复使用旧译文进行迭代
    if "pending_keys" not in st.session_state:
        st.session_state.pending_keys = []
    if "translation_dict" not in st.session_state:
        st.session_state.translation_dict = {}

    st.header("工作流测试")
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
    
    def parse_workflow_results(workflow_results):
        """
        将 workflow 返回的结果解析成 {编号: 内容} 的 dict
        规则：
        - 等号左边是编号（任何字符）
        - 等号右边是内容
        """
        parsed = {}

        if not workflow_results:
            return parsed

        for batch in workflow_results:
            if not isinstance(batch, dict):
                continue

            items = batch.get("download_url", [])
            if not isinstance(items, list):
                continue

            for raw_item in items:
                if not raw_item or not isinstance(raw_item, str):
                    continue

                text = raw_item.strip()

                # ① 尝试解一层 JSON（处理 ["xxx=yyy"]）
                if text.startswith("[") and text.endswith("]"):
                    try:
                        decoded = json.loads(text)
                        if isinstance(decoded, list):
                            for sub in decoded:
                                _extract_kv_relaxed(sub, parsed)
                            continue
                    except Exception:
                        pass  # 解不开就当普通字符串继续

                # ② 普通字符串
                _extract_kv_relaxed(text, parsed)

        return parsed

    def _extract_kv_relaxed(text, parsed_dict):
        if not text or "=" not in text:
            return

        key, value = text.split("=", 1)

        key = key.strip()
        value = value.strip()

        # 左右都必须非空
        if not key or not value:
            return

        parsed_dict[key] = value

    def process_iteration(original_dict, translation_dict, iteration_dict, iterable_labels, custom_statuses):
        """
        Apply iteration dict to translation_dict according to iterable_labels and custom_statuses.
        Returns: (new_translation_dict, updated_records, iteration_stats, iteration_labels_count)
        """
        iteration_stats = {
            "total_in_iteration": 0,
            "matched_in_original": 0,
            "updated_translations": 0,
            "skipped_not_iterable": 0,
            "iteration_labels_distribution": {}
        }

        updated_records = []
        iteration_labels_count = {}

        if not iteration_dict:
            return translation_dict, updated_records, iteration_stats, iteration_labels_count

        iteration_stats["total_in_iteration"] = len(iteration_dict)
        iterable_labels_norm = [label.strip() for label in iterable_labels]

        before_update_translation = translation_dict.copy()

        for key, iteration_value in iteration_dict.items():
            if key in original_dict:
                iteration_stats["matched_in_original"] += 1
                original_text = original_dict[key]
                iteration_label = calculate_single_status(original_text, iteration_value, custom_statuses)
                iteration_labels_count[iteration_label] = iteration_labels_count.get(iteration_label, 0) + 1

                if iteration_label in iterable_labels_norm:
                    translation_dict[key] = iteration_value
                    iteration_stats["updated_translations"] += 1
                else:
                    iteration_stats["skipped_not_iterable"] += 1
            else:
                # key not in original, skip
                pass

        iteration_stats["iteration_labels_distribution"] = iteration_labels_count

        # build updated_records for display
        for key, iteration_value in iteration_dict.items():
            if key in original_dict:
                original_text = original_dict.get(key, "")
                old_translation = before_update_translation.get(key, "")
                iteration_label = calculate_single_status(original_text, iteration_value, custom_statuses)
                if iteration_label in iterable_labels_norm:
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

        return translation_dict, updated_records, iteration_stats, iteration_labels_count

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

        # 读取上传译文的 raw bytes 计算哈希，以区分是否为新上传（Streamlit 会在每次 rerun 中重新传入 file uploader）
        raw_bytes = translation_file.getvalue()
        file_hash = hashlib.md5(raw_bytes).hexdigest() if raw_bytes is not None else None

        parsed_translation = parse_txt(translation_file)

        # 只有当 session 中没有译文，或上传的文件内容与 session 中保存的不同，才覆盖 session 中的译文字典
        prev_hash = st.session_state.get("translation_file_hash")
        if prev_hash != file_hash or not st.session_state.get("translation_dict"):
            st.session_state.translation_dict = parsed_translation
            st.session_state.translation_file_hash = file_hash

        # 使用会话中的译文（可能是刚刚解析的，也可能是之前迭代后的译文）
        translation_dict = st.session_state.get("translation_dict", parsed_translation)

        # 初始化迭代统计
        iteration_stats = {
            "total_in_iteration": 0,
            "matched_in_original": 0,
            "updated_translations": 0,
            "skipped_not_iterable": 0,
            "iteration_labels_distribution": {}
        }

        st.write(
            "DEBUG:",
            "iteration_file =", bool(iteration_file),
            "iteration_dict size =", len(st.session_state.iteration_dict),
            "has_workflow_result =", st.session_state.has_workflow_result
        )
        st.subheader("迭代更新")
        st.info("需要先上传迭代文件，或成功执行一次 Workflow 后才能使用")

        iteration_dict = {}

        if iteration_file:
            iteration_dict = parse_txt(iteration_file)
        else:
            iteration_dict = st.session_state.iteration_dict

        st.write("DEBUG iteration_dict size:", len(iteration_dict))

        if not iteration_dict:
            st.warning("没有可用的迭代内容")
        else:
            # 使用封装函数处理迭代，这样可以被 workflow 后的按钮复用
            translation_dict, updated_records, iteration_stats, iteration_labels_count = process_iteration(
                original_dict, translation_dict, iteration_dict, iterable_labels, custom_statuses
            )

            if iteration_stats["total_in_iteration"] > 0:
                st.success("迭代文件处理完成:")

                # 创建迭代分析表格
                iteration_analysis = []
                for label, count in iteration_labels_count.items():
                    percentage = (count / iteration_stats["matched_in_original"] * 100) if iteration_stats["matched_in_original"] > 0 else 0
                    iteration_analysis.append({
                        "标签": label,
                        "数量": count,
                        "占比": f"{percentage:.1f}%",
                        "是否可迭代": "是" if label in [l.strip() for l in iterable_labels] else "否"
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

                if updated_records:
                    df_updated = pd.DataFrame(updated_records)
                    st.subheader("本次迭代更新明细")
                    st.dataframe(df_updated)
                else:
                    st.info("本次迭代没有更新任何条目（或无匹配可迭代标签）。")
                # 保存更新后的译文到 session_state，以便下一轮导出基于已接受的译文
                st.session_state.translation_dict = translation_dict
                # 从 pending_keys 中移除已被接受的编号（如果存在）
                if updated_records:
                    accepted_keys = [r.get("编号") for r in updated_records if r.get("编号")]
                    pending = st.session_state.get("pending_keys", [])
                    st.session_state.pending_keys = [k for k in pending if k not in accepted_keys]

        # ---- 重新计算最终 DataFrame（优先使用会话中已保存的译文） ----
        translation_display_dict = st.session_state.get("translation_dict", translation_dict)
        df_result = calculate_length_status(original_dict, translation_display_dict, custom_statuses)

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
        # ===== 修复：使用最新译文动态计算待翻译字段 =====
        translation_dict_runtime = st.session_state.get("translation_dict", translation_dict)
        df_result_runtime = calculate_length_status(original_dict, translation_dict_runtime, custom_statuses)

        # 根据选中的可迭代标签筛选仍需翻译的字段
        export_df_runtime = df_result_runtime[df_result_runtime["标签"].isin(
            [name for name, checked in export_checks.items() if checked]
        )]

        st.write(f"当前仍需翻译的字段数量: {len(export_df_runtime)}")

        if not export_df_runtime.empty:
            st.write(f"符合条件的字段数量: {len(export_df_runtime)}")
            split_lines = st.number_input("每个拆分文件行数（留空或 0 表示不拆分）", min_value=0, value=0, step=1, key="tab1_split_lines")

            if split_lines > 0:
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                    total_lines = len(export_df_runtime)
                    num_parts = (total_lines + split_lines - 1) // split_lines
                    st.write(f"将拆分成 {num_parts} 个文件，每个最多 {split_lines} 行。")
                    for i in range(num_parts):
                        part_df = export_df_runtime.iloc[i*split_lines:(i+1)*split_lines]
                        part_txt = "\n".join([f"{row['编号']}={row['原文']}" for idx,row in part_df.iterrows()])
                        part_name = f"筛选原文_part_{i+1}.txt"
                        zip_file.writestr(part_name, part_txt)
                zip_buffer.seek(0)
                st.download_button(label=f"下载拆分后的压缩包 ({num_parts} 个文件)",
                                   data=zip_buffer.getvalue(),
                                   file_name=f"筛选原文拆分_{int(t.time())}.zip",
                                   mime="application/zip")
            else:
                export_txt = "\n".join([f"{row['编号']}={row['原文']}" for idx,row in export_df_runtime.iterrows()])
                st.download_button(label="下载筛选结果 (.txt)",
                                   data=export_txt,
                                   file_name=f"筛选原文_{int(t.time())}.txt",
                                   mime="text/plain")

        # ---- 导出最新版翻译文件 ----
        final_translation_txt = "\n".join([f"{key}={value}" for key,value in st.session_state.get("translation_dict", translation_dict).items()])
        st.download_button(label="导出最新版的总翻译文件 (.txt)",
                           data=final_translation_txt,
                           file_name="最新翻译.txt",
                           mime="text/plain")
        
        # workflow 配置
        COZE_TOKEN = "pat_tI3FcbOnw0DsbHF4TYWemJtD1FLLCHYhtO0RBgZMaPHpAxqYnZ4UjAB3QAyItY7w"
        WORKFLOW_ID = "7582900707377446975"
        coze_client = coze(auth=TokenAuth(token=COZE_TOKEN), base_url=COZE_CN_BASE_URL)

        # 构建用于翻译的批次：优先使用会话中的 pending_keys（若为空则以当前筛选结果初始化），
        # 并保证 pending_keys 与当前筛选结果同步（移除已被标记为合格或已被接受的键）
        translation_dict_runtime = st.session_state.get("translation_dict", translation_dict)

        # 重新计算长度检查结果并筛选待翻译项（遵循当前 export_checks）
        df_result_runtime = calculate_length_status(original_dict, translation_dict_runtime, custom_statuses)
        export_df_runtime = df_result_runtime[df_result_runtime["标签"].isin([name for name, checked in export_checks.items() if checked])]

        export_keys = list(export_df_runtime["编号"])
        existing_pending = st.session_state.get("pending_keys", [])
        if not existing_pending:
            # 初次初始化 pending_keys
            st.session_state.pending_keys = export_keys
        else:
            # 保留原有顺序，但过滤掉已不再符合导出条件的键
            export_key_set = set(export_keys)
            st.session_state.pending_keys = [k for k in existing_pending if k in export_key_set]

        current_pending_keys = st.session_state.pending_keys
        field_objects = [f"{k}={original_dict[k]}" for k in current_pending_keys]
        st.info(f"待翻译队列长度: {len(current_pending_keys)}（将按该队列顺序分批发送）")

        # 构建 batch
        batch_size = 10
        batches = [field_objects[i:i+batch_size] for i in range(0, len(field_objects), batch_size)]

        DEBUG_MODE = False
        if DEBUG_MODE:
            batches = batches[:2]

        total_batches = len(batches)
        st.info(f"执行 {total_batches} 批次")

        language = st.text_input("目标语言", value="es")
        terminology = st.text_input("术语表（可选）", value="")

        def run_batch(batch, batch_index):
            results = []
            raw_events = []
            try:
                stream = coze_client.workflows.runs.stream(
                    workflow_id=WORKFLOW_ID,
                    parameters={
                        "url": batch,  # 直接传字段数组
                        "language": language,
                        "terminology": terminology
                    }
                )
                for event in stream:
                    raw_events.append(repr(event))
                    if event.event == WorkflowEventType.MESSAGE:
                        content = getattr(event.message, "content", None)
                        if content:
                            try:
                                results.append(json.loads(content))
                            except Exception:
                                results.append(content)
            except Exception as e:
                raw_events.append(f"Batch {batch_index+1} 调用失败：{e}")
            return batch_index, results, raw_events
        
        if st.button("开始调用 Workflow（并行 + 实时进度）"):
            progress_bar = st.progress(0)
            status_text = st.empty()

            all_results = [None]*total_batches
            all_raw_events = [None]*total_batches

            with ThreadPoolExecutor(max_workers=total_batches) as executor:
                futures = [executor.submit(run_batch, batch, idx) for idx, batch in enumerate(batches)]
                for i, future in enumerate(futures):
                    idx, results, raw_events = future.result()
                    all_results[idx] = results
                    all_raw_events[idx] = raw_events

                    progress = int(((i+1)/total_batches) * 100)
                    progress_bar.progress(progress)
                    status_text.text(f"已完成 {i+1}/{total_batches} 批次")

                    # st.write(f"### 批次 {idx+1} 返回结果：")
                    # st.text(json.dumps(results, ensure_ascii=False, indent=2))

            # 合并所有批次结果
            # 合并所有批次结果
            workflow_results = [item for batch in all_results if batch for item in batch]
            workflow_raw_events = [item for batch in all_raw_events if batch for item in batch]

            # ⭐⭐ 关键：调用解析函数 ⭐⭐

            st.session_state.has_workflow_result = True
            parsed_results = parse_workflow_results(workflow_results)
            st.session_state.iteration_dict = parsed_results
            can_iterate = bool(iteration_file) or st.session_state.has_workflow_result

            # 当按钮被点击时，调用 process_iteration 并展示结果
            if st.button("使用当前结果执行迭代", disabled=not can_iterate):
                iteration_dict_runtime = st.session_state.iteration_dict
                # 使用会话中保存的最新译文，避免使用旧的本地变量覆盖已经接受的译文
                translation_runtime = st.session_state.get("translation_dict", translation_dict)
                translation_dict, updated_records, iteration_stats, iteration_labels_count = process_iteration(
                    original_dict, translation_runtime, iteration_dict_runtime, iterable_labels, custom_statuses
                )

                if iteration_stats["total_in_iteration"] > 0:
                    st.success("已应用 Workflow 解析结果并更新译文字典。")
                    # 显示统计
                    iteration_analysis = []
                    for label, count in iteration_labels_count.items():
                        percentage = (count / iteration_stats["matched_in_original"] * 100) if iteration_stats["matched_in_original"] > 0 else 0
                        iteration_analysis.append({
                            "标签": label,
                            "数量": count,
                            "占比": f"{percentage:.1f}%",
                            "是否可迭代": "是" if label in [l.strip() for l in iterable_labels] else "否"
                        })
                    st.dataframe(pd.DataFrame(iteration_analysis))

                    if updated_records:
                        st.subheader("已更新条目（来自 Workflow）")
                        st.dataframe(pd.DataFrame(updated_records))
                    
                    # ⭐⭐ 关键：迭代完成后重新计算最终表格与统计，为下一轮 workflow 做准备 ⭐⭐
                    st.divider()
                    st.subheader("第二轮准备：重新计算统计与导出内容")
                    
                    # 重新计算长度检查结果
                    df_result_new = calculate_length_status(original_dict, translation_dict, custom_statuses)
                    
                    # 重新计算统计信息
                    stats_df_new = compute_statistics(df_result_new, custom_statuses, total_field="原文")
                    st.write("更新后的字段统计：")
                    st.dataframe(
                        stats_df_new.reset_index(drop=True)
                                .style
                                .set_properties(subset=["类型"], **{'text-align': 'left'})
                                .set_properties(subset=["数量","占比"], **{'text-align': 'center'})
                    )
                    
                    # 重新应用导出条件，过滤出需要继续翻译的内容
                    export_df_new = df_result_new[df_result_new["标签"].isin([name for name, checked in export_checks.items() if checked])]
                    new_field_count = len(export_df_new)
                    
                    if new_field_count > 0:
                        st.info(f"✅ 发现 **{new_field_count}** 个仍需翻译的字段（过短/过长），可继续运行下一批 Workflow。")
                        st.write("新一批需要翻译的字段示例（前5条）：")
                        st.dataframe(export_df_new.head(5)[["编号", "原文", "译文", "标签"]])
                    else:
                        st.success("🎉 所有字段已达到合格标准，无需再次迭代！")
                else:
                    st.info("Workflow 结果中未发现可应用的迭代内容。")

                # 保存更新后的译文到 session_state，供下一轮使用
                st.session_state.translation_dict = translation_dict
                # 从 pending_keys 中移除已被接受的编号（如果存在）
                if updated_records:
                    accepted_keys = [r.get("编号") for r in updated_records if r.get("编号")]
                    pending = st.session_state.get("pending_keys", [])
                    st.session_state.pending_keys = [k for k in pending if k not in accepted_keys]

            st.success("Workflow 执行完成")

            st.subheader("Workflow 输出结果（原始）")
            st.text_area(
                "Raw",
                json.dumps(workflow_results, ensure_ascii=False, indent=2),
                height=300
            )

            st.subheader("解析后的可迭代内容")
            st.text_area(
                "Parsed",
                json.dumps(parsed_results, ensure_ascii=False, indent=2),
                height=300
            )
