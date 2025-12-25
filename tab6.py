import streamlit as st
from cozepy import Coze, TokenAuth, WorkflowEventType, COZE_CN_BASE_URL
import pandas as pd
import json, io, zipfile, time, subprocess, tempfile, os
from datetime import datetime
from collections import OrderedDict
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

def tab6_content():
    st.header("Coze Workflow 调试")
    st.info("使用 PAT 调用 Coze Workflow，实时显示事件并解析 download_url")

    # ------------------------------
    # 用户输入
    # ------------------------------
    coze_token = st.text_input("输入你的 PAT（Personal Access Token）", type="password")
    workflow_id = st.text_input("Workflow ID")
    
    st.subheader("Workflow 输入参数")
    url_list = st.text_area("url (多行表示 String Array)", value="", height=80)
    terminology = st.text_input("terminology")
    language = st.text_input("language")
    
    run_workflow = st.button("运行 Workflow")

    # ------------------------------
    # 处理输入
    # ------------------------------
    if run_workflow:
        if not all([coze_token, workflow_id, url_list, terminology, language]):
            st.error("请完整填写 PAT、Workflow ID 及输入参数")
        else:
            # 解析 url 为列表
            url_array = [line.strip() for line in url_list.splitlines() if line.strip()]
            
            # 初始化 Coze 客户端
            coze = Coze(auth=TokenAuth(token=coze_token), base_url=COZE_CN_BASE_URL)
            
            # 构造 workflow 参数
            workflow_params = {
                "url": url_array,
                "terminology": terminology,
                "language": language
            }
            
            st.info("开始调用 Workflow（可能需要几秒钟）")
            
            # 创建 Streamlit 输出容器
            output_container = st.empty()
            
            try:
                # 调用 stream
                stream_iter = coze.workflows.runs.stream(
                    workflow_id=workflow_id,
                    parameters=workflow_params
                )
                
                download_urls = []

                for event in stream_iter:
                    # 显示原始事件
                    output_container.text(f"Event: {event}")

                    # MESSAGE 事件解析
                    if getattr(event, "event", None) == WorkflowEventType.MESSAGE:
                        content = getattr(event.message, "content", None)
                        if content:
                            try:
                                data = json.loads(content)
                                url = data.get("download_url")
                                if url:
                                    download_urls.append(url)
                                    st.success(f"解析到 download_url: {url}")
                            except Exception as e:
                                st.warning(f"解析 JSON 出错: {e}")

                    # ERROR 事件
                    elif getattr(event, "event", None) == WorkflowEventType.ERROR:
                        st.error(f"Workflow 出现错误: {event.error}")

                    # INTERRUPT 事件
                    elif getattr(event, "event", None) == WorkflowEventType.INTERRUPT:
                        st.warning("Workflow 被中断，需要 resume（目前未自动处理）")

                st.info("Workflow 执行完毕")
                if download_urls:
                    st.success(f"获取到 {len(download_urls)} 个 download_url")
                    for idx, url in enumerate(download_urls, 1):
                        st.write(f"{idx}. {url}")
                else:
                    st.warning("未从输出中找到 download_url")

            except Exception as e:
                st.error(f"Workflow 调用失败: {e}")
