import streamlit as st
import pandas as pd
import json, io, zipfile, time, subprocess, tempfile, os, re
from datetime import datetime
from collections import OrderedDict
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

from tab1 import tab1_content
from tab2 import tab2_content
from tab3 import tab3_content
from tab4 import tab4_content
from tab5 import tab5_content
from tab6 import tab6_content
from tab7 import tab7_content
from tab8 import tab8_content
from tab9 import tab9_content

st.set_page_config(
    page_title="本地化工作流辅助工具",
    layout="wide",
    initial_sidebar_state="expanded"
)

col1, col2 = st.columns([8, 1])
col1.title("本地化工作流辅助工具")
col2.markdown("<small>Version 4.0</small>", unsafe_allow_html=True)

TAB_NAMES = ["长度检查", "合并文件", "拆分文件", "整合工作台", "Coze 测试", "工作流测试", "自动化迭代", "DGame 格式整理"]

# 在 session_state 中初始化当前选中的标签
if 'current_tab' not in st.session_state:
    st.session_state.current_tab = TAB_NAMES[0]

# 创建sidebar按钮列表
st.sidebar.markdown("### 功能导航")
st.sidebar.markdown("---")

# 为每个标签创建一个按钮
for tab in TAB_NAMES:
    # 如果按钮被点击，更新当前选中的标签
                if st.sidebar.button(tab, key=f"btn_{tab}", width=stretch):
                        st.session_state.current_tab = tab

# 添加一些视觉分隔
st.sidebar.markdown("---")

# 显示当前选中的标签（可选）
st.sidebar.info(f"当前页面: **{st.session_state.current_tab}**")

# 根据选中的标签显示内容
if st.session_state.current_tab == "长度检查":
    tab1_content()
elif st.session_state.current_tab == "合并文件":
    tab3_content()
elif st.session_state.current_tab == "拆分文件":
    tab4_content()
elif st.session_state.current_tab == "整合工作台":
    tab5_content()
elif st.session_state.current_tab == "Coze 测试":
    tab6_content()
elif st.session_state.current_tab == "工作流测试":
    tab7_content()
elif st.session_state.current_tab == "自动化迭代":
    tab8_content()
elif st.session_state.current_tab == "DGame 格式整理":
    tab9_content()

