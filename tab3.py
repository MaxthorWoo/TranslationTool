import streamlit as st
import pandas as pd
import json, io, zipfile, time, subprocess, tempfile, os
from datetime import datetime
from collections import OrderedDict
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

def tab3_content():
    st.header("多语言文件合并")
    st.info("上传多个翻译文件（txt/ini，格式：编号=内容），并为每个文件自定义列名，合并生成 Excel 文件。")

    # ---------------------------
    # 文件上传
    # ---------------------------
    uploaded_files = st.file_uploader(
        "上传多语言文件 (.txt/.ini)", 
        type=['txt','ini'], 
        accept_multiple_files=True
    )

    # ---------------------------
    # 工具函数
    # ---------------------------
    def parse_ini_file(file):
        """解析单个文件（BytesIO）返回编号->内容字典"""
        content_dict = {}
        for line_num, line in enumerate(file.getvalue().decode('utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith(';') or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                content_dict[key.strip()] = value.strip()
        return content_dict

    def merge_files_to_excel(files, custom_names):
        """
        合并多个文件到 DataFrame，列名使用 custom_names
        返回 df
        """
        language_data = OrderedDict()
        language_names = []

        # 解析文件
        for idx, file in enumerate(files):
            data = parse_ini_file(file)
            col_name = custom_names[idx]
            language_data[col_name] = data
            language_names.append(col_name)

        # 确定基础语言（首个文件为基础）
        base_language = language_names[0]
        
        # 基础语言编号顺序
        base_keys_ordered = list(language_data[base_language].keys())
        
        # 所有编号
        all_keys = set()
        for d in language_data.values():
            all_keys.update(d.keys())
        
        # 创建结果有序字典
        result_data = OrderedDict()
        result_data['编号'] = []
        for lang in language_names:
            result_data[lang] = []

        # 添加基础语言编号
        processed_keys = set()
        for key in base_keys_ordered:
            result_data['编号'].append(key)
            for lang in language_names:
                result_data[lang].append(language_data[lang].get(key, ''))
            processed_keys.add(key)

        # 添加其他文件独有编号
        remaining_keys = all_keys - processed_keys
        for key in sorted(remaining_keys):
            result_data['编号'].append(key)
            for lang in language_names:
                result_data[lang].append(language_data[lang].get(key, ''))

        df = pd.DataFrame(result_data)
        return df

    # ---------------------------
    # 用户自定义列名
    # ---------------------------
    custom_names = []
    if uploaded_files:
        st.subheader("为每个上传的文件自定义列名")
        for file in uploaded_files:
            default_name = os.path.splitext(file.name)[0]
            name = st.text_input(f"{file.name} 命名", value=default_name)
            custom_names.append(name)

    # ---------------------------
    # 执行合并
    # ---------------------------
    if uploaded_files and all(custom_names):
        with st.spinner("正在解析和合并文件..."):
            df = merge_files_to_excel(uploaded_files, custom_names)
        
        # 显示统计信息
        st.subheader("合并统计信息")
        st.write(f"总条目数: {len(df)}")
        
        missing_info = []
        for lang in custom_names:
            missing_count = (df[lang] == '').sum()
            missing_info.append({'语言': lang, '缺失条目数': missing_count})
        st.table(pd.DataFrame(missing_info))
        
        # Excel 下载
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, engine='openpyxl')
        buffer.seek(0)
        st.download_button(
            label="下载合并后的 Excel 文件",
            data=buffer,
            file_name="combined_languages.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        # 显示前10条内容预览
        st.subheader("合并文件预览（前10条）")
        st.dataframe(df.head(10))