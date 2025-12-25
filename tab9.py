import streamlit as st
import pandas as pd
import io
from datetime import datetime

def tab9_content():
    # 设置页面配置
    st.set_page_config(
        page_title="Excel转TXT/INI工具",
        page_icon="📊",
        layout="wide"
    )

    # 页面标题和描述
    st.title("📊 Excel转TXT/INI转换工具")
    st.markdown("""
    这个工具可以将包含ID和Lang列的Excel文件转换为TXT或INI格式。
    转换后的格式为：`ID=Lang`
    """)

    # 在侧边栏添加说明
    with st.sidebar:
        st.header("📋 使用说明")
        st.markdown("""
        1. **上传Excel文件**：支持.xlsx或.xls格式
        2. **选择输出格式**：TXT或INI格式
        3. **预览转换结果**：查看转换后的内容
        4. **下载文件**：获取转换后的文件
        
        **文件格式要求：**
        - 必须有 **ID** 和 **Lang** 两列
        - ID列：编号/ID
        - Lang列：文本内容
        """)
        
        st.header("🎯 输出示例")
        st.code("""
    17637612=直接花费金币，立即完成士兵训练
    466887785=直接花费金币，立即完成加速
    2587312713=直接花费金币，立即完成建筑建造
    2716359484=直接花费金币，立即完成科技研究
        """, language="text")

    # 文件上传区域
    st.header("📤 1. 上传Excel文件")
    uploaded_file = st.file_uploader(
        "选择Excel文件",
        type=['xlsx', 'xls'],
        help="请上传包含ID和Lang列的Excel文件"
    )

    if uploaded_file is not None:
        try:
            # 读取Excel文件
            df = pd.read_excel(uploaded_file, engine='openpyxl')
            
            # 显示文件信息
            col1, col2 = st.columns(2)
            with col1:
                st.success(f"✅ 文件读取成功")
                st.info(f"**文件名：** {uploaded_file.name}")
            with col2:
                st.info(f"**数据形状：** {df.shape[0]} 行 × {df.shape[1]} 列")
                st.info(f"**列名：** {list(df.columns)}")
            
            # 检查必要的列
            required_columns = ['ID', 'Lang']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                st.error(f"❌ 缺少必要的列: {missing_columns}")
                st.warning("请确保Excel文件包含'ID'和'Lang'列")
            else:
                # 显示数据预览
                st.header("👁️ 2. 数据预览")
                
                # 让用户选择显示的行数
                preview_rows = st.slider("选择预览行数", 5, 50, 10)
                
                # 显示数据表格
                st.dataframe(df.head(preview_rows), use_container_width=True)
                
                # 显示数据统计
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("总行数", df.shape[0])
                with col2:
                    valid_id = df['ID'].notna().sum()
                    st.metric("有效ID数", valid_id)
                with col3:
                    valid_lang = df['Lang'].notna().sum()
                    st.metric("有效内容数", valid_lang)
                
                # 格式选择区域
                st.header("🔄 3. 选择输出格式")
                
                col1, col2 = st.columns([1, 2])
                with col1:
                    output_format = st.radio(
                        "选择输出格式",
                        ['TXT', 'INI'],
                        horizontal=True
                    )
                
                # 转换按钮
                if st.button("🚀 开始转换", type="primary", use_container_width=True):
                    with st.spinner("正在转换..."):
                        # 生成转换后的内容
                        converted_lines = []
                        valid_count = 0
                        skipped_count = 0
                        
                        for index, row in df.iterrows():
                            # 获取ID和Lang
                            id_value = str(row['ID']).strip()
                            lang_value = str(row['Lang']).strip() if pd.notna(row['Lang']) else ""
                            
                            # 跳过空值
                            if not id_value or id_value == 'nan' or not lang_value or lang_value == 'nan':
                                skipped_count += 1
                                continue
                            
                            # 添加转换后的行
                            converted_lines.append(f"{id_value}={lang_value}")
                            valid_count += 1
                        
                        # 显示转换统计
                        st.success(f"✅ 转换完成！")
                        
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("总行数", df.shape[0])
                        with col2:
                            st.metric("转换成功", valid_count)
                        with col3:
                            st.metric("跳过行数", skipped_count)
                        
                        # 显示转换后的内容预览
                        st.header("📄 4. 转换结果预览")
                        
                        if converted_lines:
                            # 创建预览区域
                            preview_text = "\n".join(converted_lines[:10])
                            st.text_area(
                                "转换结果（前10行）",
                                preview_text,
                                height=200,
                                help="显示转换后的前10行内容"
                            )
                            
                            # 显示完整的转换内容
                            with st.expander("查看完整转换结果"):
                                full_text = "\n".join(converted_lines)
                                st.text_area("完整内容", full_text, height=300)
                            
                            # 下载区域
                            st.header("💾 5. 下载文件")
                            
                            # 生成文件名
                            original_name = uploaded_file.name.split('.')[0]
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            download_filename = f"{original_name}_{timestamp}.{output_format.lower()}"
                            
                            # 创建下载按钮
                            converted_text = "\n".join(converted_lines)
                            st.download_button(
                                label=f"⬇️ 下载 {output_format} 文件",
                                data=converted_text,
                                file_name=download_filename,
                                mime="text/plain",
                                type="primary",
                                use_container_width=True
                            )
                            
                            # 提供复制按钮
                            if st.button("📋 复制到剪贴板", use_container_width=True):
                                # 在Streamlit中，我们可以使用st.code让用户手动复制
                                st.code(converted_text, language="text")
                                st.success("已将内容显示为代码格式，您可以手动复制")
                        else:
                            st.warning("⚠️ 转换结果为空，请检查数据")
                
                # 额外的格式选项
                with st.expander("⚙️ 高级选项"):
                    st.checkbox("跳过空行", value=True, disabled=True)
                    st.checkbox("去除首尾空格", value=True, disabled=True)
                    col1, col2 = st.columns(2)
                    with col1:
                        st.checkbox("自动排序", value=False)
                    with col2:
                        st.checkbox("去除重复项", value=False)
        
        except Exception as e:
            st.error(f"❌ 处理文件时出错: {str(e)}")
            st.code(f"错误详情：\n{e}", language="text")

    # 如果没有上传文件，显示示例
    else:
        st.info("👈 请在左侧上传Excel文件开始转换")
        
        # 显示示例数据
        with st.expander("📋 查看示例Excel格式"):
            st.markdown("""
            Excel文件应该包含以下两列：
            
            | ID | Lang |
            |----|------|
            | 17637612 | 直接花费金币，立即完成士兵训练 |
            | 466887785 | 直接花费金币，立即完成加速 |
            | 2587312713 | 直接花费金币，立即完成建筑建造 |
            | 2716359484 | 直接花费金币，立即完成科技研究 |
            """)
        
        # 提供示例文件下载
        with st.expander("📥 下载示例Excel文件"):
            # 创建示例数据
            sample_data = {
                'ID': [17637612, 466887785, 2587312713, 2716359484],
                'Lang': [
                    '直接花费金币，立即完成士兵训练',
                    '直接花费金币，立即完成加速',
                    '直接花费金币，立即完成建筑建造',
                    '直接花费金币，立即完成科技研究'
                ]
            }
            sample_df = pd.DataFrame(sample_data)
            
            # 转换为Excel字节流
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                sample_df.to_excel(writer, index=False, sheet_name='Sheet1')
            
            # 提供下载
            st.download_button(
                label="⬇️ 下载示例Excel文件",
                data=output.getvalue(),
                file_name="示例文件.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

    # 页脚
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center'>
        <p>✨ Excel转TXT/INI转换工具 | 支持拖拽上传，实时预览，一键下载</p>
    </div>
    """, unsafe_allow_html=True)