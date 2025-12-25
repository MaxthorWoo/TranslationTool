@echo off
:: 进入脚本所在目录
cd /d "%~dp0"
:: 激活虚拟环境（如果你有）
:: call venv\Scripts\activate
:: 启动 Streamlit
streamlit run app.py
pause
