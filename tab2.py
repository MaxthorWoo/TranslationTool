import streamlit as st
import pandas as pd
import json, io, zipfile, time, subprocess, tempfile, os
from datetime import datetime
from collections import OrderedDict
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

def tab2_content():
    st.header("批量上传到 0x0.st")
    st.info("上传多个文件到 0x0.st，并生成包含所有成功上传链接的 JSON 文件。支持分批下载。")

    # 初始化 session_state
    if "uploaded_files" not in st.session_state:
        st.session_state.uploaded_files = []
    if "success_links" not in st.session_state:
        st.session_state.success_links = []
    if "failed_links" not in st.session_state:
        st.session_state.failed_links = []
    if "part_files" not in st.session_state:
        st.session_state.part_files = []
    if "all_filename" not in st.session_state:
        st.session_state.all_filename = ""
    if "timestamp" not in st.session_state:
        st.session_state.timestamp = ""

    # 上传文件
    uploaded_files = st.file_uploader("上传多个文件", accept_multiple_files=True)
    st.session_state.uploaded_files = uploaded_files

    # 分批数量
    split_count = st.number_input("分成几批？", min_value=1, value=1, step=1, key="tab2_split_count")

    # 定义上传函数
    def upload_file(file):
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(file.getbuffer())
        tmp.flush()
        tmp.close()
        try:
            result = subprocess.run(
                ["curl", "-s", "-F", f"file=@{tmp.name}", "https://0x0.st"],
                capture_output=True, text=True
            )
            url = result.stdout.strip()
            return url
        finally:
            os.unlink(tmp.name)

    # 开始上传按钮
    if st.button("开始上传") and uploaded_files:
        st.info(f"开始上传 {len(uploaded_files)} 个文件...")
        st.session_state.success_links = []
        st.session_state.failed_links = []

        for file in uploaded_files:
            st.write(f"上传中: {file.name}")
            url = upload_file(file)  # 这里调用正确的上传函数
            if url.startswith("https://"):
                st.success(f"上传成功: {url}")
                st.session_state.success_links.append(url)
            else:
                st.error(f"上传失败: {url}")
                st.session_state.failed_links.append({"file": file.name, "error": url})

        st.session_state.timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        total_success = len(st.session_state.success_links)
        total_fail = len(st.session_state.failed_links)

        st.write(f"✅ 成功上传: {total_success} 个文件")
        st.write(f"❌ 上传失败: {total_fail} 个文件")
        if total_fail > 0:
            st.json(st.session_state.failed_links)

        if total_success > 0:
            # 分批 JSON 文件
            base_count = total_success // split_count
            remainder = total_success % split_count
            st.write(f"总共 {total_success} 个链接，将分成 {split_count} 批：每批 {base_count} 个链接，前 {remainder} 批多 1 个链接。")

            st.session_state.part_files = []
            current_index = 0
            for i in range(1, split_count + 1):
                part_count = base_count + 1 if i <= remainder else base_count
                part_links = st.session_state.success_links[current_index:current_index + part_count]
                current_index += part_count

                part_filename = f"links_part_{i}_{st.session_state.timestamp}.json"
                with open(part_filename, "w", encoding="utf-8") as f:
                    json.dump(part_links, f, ensure_ascii=False, indent=4)
                st.session_state.part_files.append((part_filename, part_links))

            # 总 JSON 文件
            st.session_state.all_filename = f"links_all_{st.session_state.timestamp}.json"
            with open(st.session_state.all_filename, "w", encoding="utf-8") as f:
                json.dump(st.session_state.success_links, f, ensure_ascii=False, indent=4)

    # 下载按钮与 JSON 预览
    if st.session_state.success_links:
        st.subheader("下载 JSON 文件")
        st.info("点击下面的按钮下载生成的 JSON 文件。")

        st.download_button(
            "下载全部链接 JSON",
            data=open(st.session_state.all_filename, "r", encoding="utf-8").read(),
            file_name=st.session_state.all_filename,
            mime="application/json"
        )

        for part_filename, _ in st.session_state.part_files:
            st.download_button(
                f"下载 {part_filename}",
                data=open(part_filename, "r", encoding="utf-8").read(),
                file_name=part_filename,
                mime="application/json"
            )

        st.success("批量上传与分批完成！")

        # JSON 预览
        st.text_area(
            "全部链接预览",
            value=json.dumps(st.session_state.success_links, ensure_ascii=False, indent=4),
            height=min(len(st.session_state.success_links)*60, 800)
        )