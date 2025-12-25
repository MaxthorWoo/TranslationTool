import streamlit as st
import pandas as pd
import json, io, zipfile, time, subprocess, tempfile, os
from datetime import datetime
from collections import OrderedDict
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

def tab4_content():
    st.header("拆分文件")
    st.info("上传 Excel 文件，根据列名生成各语言 TXT/INI 文件，每行格式为 '编号=内容'。")

    # ---------------------------
    # 上传 Excel 文件
    # ---------------------------
    uploaded_file = st.file_uploader("上传 XLSX 文件", type=['xlsx', 'xls'])

    # ---------------------------
    # 工具函数
    # ---------------------------
    def preview_excel(df, num_rows=5):
        st.subheader("Excel 文件预览")
        st.write(f"总行数: {len(df)}, 总列数: {len(df.columns)}")
        st.dataframe(df.head(num_rows))

    def export_language_files(df, output_format="txt"):
        """
        根据 DataFrame 拆分生成各语言文件
        """
        created_files = {}
        
        # 检查 '编号' 列
        if '编号' not in df.columns:
            st.error("Excel文件中缺少 '编号' 列！")
            return created_files

        language_columns = [col for col in df.columns if col != '编号']

        # 输出到内存（BytesIO）对象，用于下载
        for lang in language_columns:
            buffer = io.StringIO()
            for _, row in df.iterrows():
                id_value = str(row['编号']).strip()
                cell_value = "" if pd.isna(row[lang]) else str(row[lang])
                buffer.write(f"{id_value}={cell_value}\n")
            buffer.seek(0)
            filename = f"{lang}.{output_format}"
            created_files[filename] = buffer.getvalue()
        
        return created_files

    # ---------------------------
    # 用户交互
    # ---------------------------
    if uploaded_file:
        try:
            df = pd.read_excel(uploaded_file, engine='openpyxl')
            preview_excel(df)

            st.subheader("输出设置")
            output_format = st.radio("选择输出文件格式", options=['txt', 'ini'], index=0)

            if st.button("生成并下载文件"):
                files_dict = export_language_files(df, output_format)
                st.success(f"已生成 {len(files_dict)} 个文件")
                
                for filename, content in files_dict.items():
                    st.download_button(
                        label=f"下载 {filename}",
                        data=content,
                        file_name=filename,
                        mime="text/plain"
                    )
        except Exception as e:
            st.error(f"读取 Excel 文件出错: {e}")
