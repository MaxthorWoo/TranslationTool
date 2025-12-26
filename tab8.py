import streamlit as st
import pandas as pd
import json, io, zipfile, tempfile, os, re, time, hashlib
from datetime import datetime
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from cozepy import Coze as coze, TokenAuth, WorkflowEventType, COZE_CN_BASE_URL

def tab8_content():
    """
    自动化翻译迭代工作台：
    1. 上传原文、译文
    2. 配置标签（自定义标签、可迭代标签）
    3. 选择导出条件
    4. 点击"开始自动化"后每 5 秒自动循环：筛选 → Workflow → 迭代 → 更新译文
    5. 停止条件：用户点停止/导出、或待翻译字段 ≤ 50
    """
    
    # 初始化会话变量
    if "auto_running" not in st.session_state:
        st.session_state.auto_running = False
    if "auto_logs" not in st.session_state:
        st.session_state.auto_logs = []
    if "auto_translation_dict" not in st.session_state:
        st.session_state.auto_translation_dict = {}
    if "auto_pending_keys" not in st.session_state:
        st.session_state.auto_pending_keys = []
    if "translation_file_hash" not in st.session_state:
        st.session_state.translation_file_hash = None
    if "auto_loop_count" not in st.session_state:
        st.session_state.auto_loop_count = 0

    st.header("自动化翻译迭代")
    st.info("上传原文和翻译文件后，自动循环执行 Workflow 调用和迭代，直到达成停止条件。")

    # 上传文件
    original_file = st.file_uploader("上传原文文件 (.txt)", type="txt", key="tab8_original")
    translation_file = st.file_uploader("上传翻译文件 (.txt)", type="txt", key="tab8_translation")

    # 公用解析函数（复用 tab7 逻辑）
    def parse_txt(file):
        result = {}
        text = file.getvalue().decode("utf-8-sig", errors="replace")
        for line in text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key:
                    result[key] = value
        return result

    def parse_workflow_results(workflow_results):
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
                if text.startswith("[") and text.endswith("]"):
                    try:
                        decoded = json.loads(text)
                        if isinstance(decoded, list):
                            for sub in decoded:
                                _extract_kv_relaxed(sub, parsed)
                            continue
                    except Exception:
                        pass
                _extract_kv_relaxed(text, parsed)
        return parsed

    def _extract_kv_relaxed(text, parsed_dict):
        if not text or "=" not in text:
            return
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            return
        parsed_dict[key] = value

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

    def process_iteration(original_dict, translation_dict, iteration_dict, iterable_labels, custom_statuses):
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

        iteration_stats["iteration_labels_distribution"] = iteration_labels_count

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

    if original_file and translation_file:
        # 解析原文
        original_dict = parse_txt(original_file)

        # 解析译文，使用哈希判断是否为新上传
        raw_bytes = translation_file.getvalue()
        file_hash = hashlib.md5(raw_bytes).hexdigest() if raw_bytes is not None else None
        parsed_translation = parse_txt(translation_file)

        # 只有当 session 中没有译文，或上传的文件内容与 session 中保存的不同，才覆盖 session 中的译文字典
        prev_hash = st.session_state.get("translation_file_hash")
        if prev_hash != file_hash or not st.session_state.get("auto_translation_dict"):
            st.session_state.auto_translation_dict = parsed_translation
            st.session_state.translation_file_hash = file_hash
            # 新上传时重置自动化状态
            st.session_state.auto_running = False
            st.session_state.auto_logs = []
            st.session_state.auto_loop_count = 0

        translation_dict = st.session_state.get("auto_translation_dict", parsed_translation)

        # ---- 标签配置 ----
        st.subheader("配置标签")
        col1, col2 = st.columns(2)

        with col1:
            st.write("**自定义标签设置**（用于初始统计和导出筛选）")
            default_statuses = [
                {"name": "合格", "min": -0.4, "max": 2, "color": "#00A000"},
                {"name": "过短", "min": -99999, "max": -0.4, "color": "#0071A6"},
                {"name": "过长", "min": 2, "max": 99999, "color": "#A60000"}
            ]
            status_count = st.number_input("标签数量", min_value=1, max_value=10, value=len(default_statuses), step=1, key="tab8_status_count")
            custom_statuses = []
            for i in range(status_count):
                default = default_statuses[i] if i < len(default_statuses) else {"name": f"标签{i+1}", "min": -99999, "max": 99999, "color": "#FFFFFF"}
                col_name, col_min, col_max = st.columns([2, 1, 1])
                with col_name:
                    name = st.text_input(f"标签{i+1} 名称", value=default["name"], key=f"tab8_status_name_{i}")
                with col_min:
                    min_val = st.number_input(f"最小值", value=float(default["min"]), key=f"tab8_status_min_{i}")
                with col_max:
                    max_val = st.number_input(f"最大值", value=float(default["max"]), key=f"tab8_status_max_{i}")
                custom_statuses.append({"name": name.strip(), "min": min_val, "max": max_val, "color": default["color"]})

        with col2:
            st.write("**可迭代标签选择**（用于过滤迭代结果）")
            iterable_labels = st.multiselect(
                "选择哪些标签的结果可以被迭代",
                options=[s["name"] for s in custom_statuses],
                default=[custom_statuses[0]["name"]],
                key="tab8_iterable_labels"
            )

        # ---- 导出条件 ----
        st.subheader("选择导出条件（筛选待翻译字段）")
        export_checks = {}
        for s in custom_statuses:
            export_checks[s["name"]] = st.checkbox(f"{s['name']}字段", value=(s["name"] in ["过短","过长"]), key=f"tab8_export_{s['name']}")

        # ---- 自动化参数 ----
        st.subheader("自动化参数")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            loop_interval = st.number_input("循环间隔（秒）", min_value=1, max_value=60, value=5, step=1, key="tab8_loop_interval")
        with col2:
            threshold = st.number_input("停止阈值（待翻译字段数≤此值时停止）", min_value=1, max_value=500, value=50, step=10, key="tab8_threshold")
        with col3:
            token = st.text_input("Token", value="1234567890", key="tab8_token")
        with col4:
            coze_api = st.text_input("工作流 API", value="7582900707377446975", key="tab8_coze_api")

        st.subheader("翻译参数")
        col1, col2 = st.columns(2)
        with col1:
            target_language = st.text_input("目标语言", value="输入语言", key="tab8_target_language")
        with col2:
            terminology = st.text_input("术语库链接（可选）", value="", key="tab8_terminology")

        # ---- 自动化日志容器 ----
        log_container = st.container()
        with log_container:
            st.subheader("自动化执行日志")
            log_area = st.empty()

        # ---- 初始化 Workflow 客户端 ----
        COZE_TOKEN = token
        WORKFLOW_ID = coze_api
        coze_client = coze(auth=TokenAuth(token=COZE_TOKEN), base_url=COZE_CN_BASE_URL)

        # ---- 自动化核心逻辑 ----
        def auto_iterate_loop(original_dict, translation_dict, custom_statuses, iterable_labels, export_checks, loop_interval, threshold, language="es", terminology=""):
            """
            自动化循环：筛选 → Workflow → 迭代
            返回最终更新的 translation_dict 和日志列表
            """
            logs = []
            loop_count = 0

            while st.session_state.auto_running:
                loop_count += 1
                logs.append(f"\n{'='*60}")
                logs.append(f"第 {loop_count} 轮迭代开始 (时间: {datetime.now().strftime('%H:%M:%S')})")
                logs.append(f"{'='*60}")

                # 计算待翻译字段
                df_result_runtime = calculate_length_status(original_dict, translation_dict, custom_statuses)
                export_df_runtime = df_result_runtime[df_result_runtime["标签"].isin([name for name, checked in export_checks.items() if checked])]
                
                pending_count = len(export_df_runtime)
                logs.append(f"当前待翻译字段数: {pending_count}")

                # 检查停止条件：待翻译字段 ≤ 阈值
                if pending_count <= threshold:
                    logs.append(f"✅ 待翻译字段数 ({pending_count}) ≤ 阈值 ({threshold})，自动停止")
                    st.session_state.auto_running = False
                    break

                # 构建批次
                export_keys = list(export_df_runtime["编号"])
                field_objects = [f"{k}={original_dict[k]}" for k in export_keys]
                batch_size = 10
                batches = [field_objects[i:i+batch_size] for i in range(0, len(field_objects), batch_size)]
                total_batches = len(batches)
                logs.append(f"构建 {total_batches} 批次，每批最多 10 条")

                # 并行调用 Workflow
                def run_batch(batch, batch_index):
                    results = []
                    try:
                        stream = coze_client.workflows.runs.stream(
                            workflow_id=WORKFLOW_ID,
                            parameters={
                                "url": batch,
                                "language": language,
                                "terminology": terminology
                            }
                        )
                        for event in stream:
                            if event.event == WorkflowEventType.MESSAGE:
                                content = getattr(event.message, "content", None)
                                if content:
                                    try:
                                        results.append(json.loads(content))
                                    except Exception:
                                        results.append(content)
                    except Exception as e:
                        logs.append(f"❌ 批次 {batch_index+1} 调用失败: {e}")
                    return batch_index, results

                # 创建实时进度容器
                progress_container = st.container()
                progress_bar = progress_container.progress(0)
                status_text = progress_container.empty()
                batch_results_text = progress_container.empty()
                
                all_results = [None] * total_batches
                batch_summaries = []
                
                with ThreadPoolExecutor(max_workers=min(total_batches, 5)) as executor:
                    futures = {executor.submit(run_batch, batch, idx): idx for idx, batch in enumerate(batches)}
                    completed = 0
                    
                    for future in futures:
                        idx, results = future.result()
                        all_results[idx] = results
                        completed += 1
                        
                        # 更新进度
                        progress_percent = int((completed / total_batches) * 100)
                        progress_bar.progress(progress_percent)
                        
                        # 统计结果
                        result_count = len(results)
                        batch_summaries.append(f"批次 {idx+1}: {result_count} 条")
                        
                        # 显示实时状态
                        active_tasks = total_batches - completed
                        status_text.text(f"⏳ 已完成: {completed}/{total_batches} | 活跃任务: {active_tasks}")
                        batch_results_text.markdown(
                            f"**批次进度详情**\n\n" + 
                            "\n".join([f"✅ {s}" for s in batch_summaries]) +
                            f"\n\n**总计获得: {sum(len(all_results[i]) if all_results[i] else 0 for i in range(len(all_results)))} 条**"
                        )

                workflow_results = [item for batch in all_results if batch for item in batch]
                logs.append(f"✅ Workflow 调用完成，获得 {len(workflow_results)} 条结果")
                logs.append(f"📋 批次汇总: {' | '.join(batch_summaries)}")

                # 解析 Workflow 结果
                parsed_results = parse_workflow_results(workflow_results)
                logs.append(f"✅ 解析结果: {len(parsed_results)} 条有效内容")

                # 执行迭代
                if parsed_results:
                    translation_dict, updated_records, iteration_stats, iteration_labels_count = process_iteration(
                        original_dict, translation_dict, parsed_results, iterable_labels, custom_statuses
                    )
                    logs.append(f"📊 迭代统计:")
                    logs.append(f"  - 总条目: {iteration_stats['total_in_iteration']}")
                    logs.append(f"  - 匹配原文: {iteration_stats['matched_in_original']}")
                    logs.append(f"  - 已更新: {iteration_stats['updated_translations']}")
                    logs.append(f"  - 被跳过: {iteration_stats['skipped_not_iterable']}")
                    logs.append(f"  - 标签分布: {iteration_stats['iteration_labels_distribution']}")
                    
                    # 添加可视化继国次计数器
                    st.session_state.auto_loop_count = loop_count
                    
                    # 添加流曙计数
                    col_stats1, col_stats2, col_stats3, col_stats4 = st.columns(4)
                    with col_stats1:
                        st.metric("总条目", iteration_stats['total_in_iteration'])
                    with col_stats2:
                        st.metric("匹配原文", iteration_stats['matched_in_original'])
                    with col_stats3:
                        st.metric("已更新", iteration_stats['updated_translations'], delta=f"+{iteration_stats['updated_translations']}")
                    with col_stats4:
                        st.metric("被跳过", iteration_stats['skipped_not_iterable'])
                else:
                    logs.append(f"⚠️ 未获得有效迭代内容")

                # 更新会话
                st.session_state.auto_translation_dict = translation_dict
                st.session_state.auto_loop_count = loop_count

                # 检查用户是否点击停止（通过超时机制）
                logs.append(f"等待 {loop_interval} 秒后进行下一轮...")
                for i in range(loop_interval):
                    if not st.session_state.auto_running:
                        logs.append("✋ 用户已停止自动化")
                        break
                    time.sleep(1)

            logs.append(f"\n{'='*60}")
            logs.append(f"自动化完成 (总轮数: {loop_count})")
            logs.append(f"{'='*60}")
            return translation_dict, logs

        # ---- UI 控制 ----
        col1, col2, col3 = st.columns([2, 2, 2])

        with col1:
            if st.button("🚀 开始自动化", key="tab8_start"):
                st.session_state.auto_running = True
                st.session_state.auto_logs = []
                st.session_state.auto_loop_count = 0

        with col2:
            if st.button("⏹️ 停止自动化", key="tab8_stop"):
                st.session_state.auto_running = False

        with col3:
            if st.button("📥 导出最新译文并停止", key="tab8_export_stop"):
                st.session_state.auto_running = False

        # ---- 执行自动化循环 ----
        if st.session_state.auto_running:
            translation_dict, logs = auto_iterate_loop(
                original_dict,
                st.session_state.auto_translation_dict,
                custom_statuses,
                iterable_labels,
                export_checks,
                loop_interval,
                threshold,
                language=target_language,
                terminology=terminology
            )
            st.session_state.auto_translation_dict = translation_dict
            st.session_state.auto_logs.extend(logs)

        # 显示日志
        if st.session_state.auto_logs:
            log_text = "\n".join(st.session_state.auto_logs)
            log_area.text_area("执行日志", value=log_text, height=400, disabled=True, key="tab8_log_display")

        # ---- 导出当前最新译文 ----
        st.subheader("导出结果")
        final_translation_dict = st.session_state.get("auto_translation_dict", translation_dict)
        final_translation_txt = "\n".join([f"{key}={value}" for key, value in final_translation_dict.items()])
        
        st.download_button(
            label="📥 下载最新版翻译文件 (.txt)",
            data=final_translation_txt,
            file_name=f"自动化翻译_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            mime="text/plain",
            key="tab8_download"
        )

        # 显示统计信息
        st.subheader("当前翻译统计")
        df_final = calculate_length_status(original_dict, final_translation_dict, custom_statuses)
        stats_data = []
        for s in custom_statuses:
            count = (df_final["标签"] == s["name"]).sum()
            ratio = (count / len(df_final) * 100) if len(df_final) > 0 else 0
            stats_data.append({"标签": s["name"], "数量": count, "占比": f"{ratio:.2f}%"})
        stats_df = pd.DataFrame(stats_data)
        st.dataframe(stats_df, use_container_width=True)

        # 显示自动化循环次数
        st.info(f"已执行循环次数: {st.session_state.auto_loop_count}")


