import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
from openai import OpenAI
from datetime import datetime, timedelta

# ==========================================
# 1. 用户配置区 (请在此填写你的信息)
# ==========================================
GOOGLE_API_KEY =  st.secrets["GOOGLE_API_KEY"]  # 使用模型Gemini 2.5 Flash
GMAIL_ADDRESS = "lishylishylishy123@gmail.com"        # 建议填写：用于 yfinance 下载标识
DEFAULT_TICKERS = ["GOOGL", "NVDA", "TSLA", "GC=F", "BTC-USD"] # 默认追踪：谷歌、英伟达、特斯拉、黄金、比特币

# ==========================================
# 2. 核心功能函数
# ==========================================
def fetch_finance_data(tickers):
    """功能：从网络增量抓取最新股价并返回 DataFrame"""
    # 抓取最近一个月的数据
    data = yf.download(tickers, period="1mo", interval="1d")
    # 只取收盘价
    if 'Close' in data.columns:
        return data['Close']
    return data

def get_ai_analysis(corr_df):
    """功能：将数学矩阵发给 AI，换取文字诊断"""
    client = OpenAI(
        api_key=GOOGLE_API_KEY, 
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    
    # 构造给 AI 的指令（Prompt）
    matrix_str = corr_df.round(2).to_string()
    prompt = f"""
    你是一名资深宏观策略分析师。以下是最近 30 天各资产的相关性矩阵：
    {matrix_str}
    
    请执行以下任务：
    1. 识别出当前相关性最高的两组资产，并解释其背后的市场逻辑。
    2. 如果我是 Google (GOOGL) 的持仓者，根据这份矩阵，我应该关注哪个宏观指标（如黄金或比特币）的走势来避险？
    3. 给出一条字数在 100 字以内的行动建议。
    """
    
    response = client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response.choices[0].message.content

# ==========================================
# 3. Streamlit 网页前端界面
# ===========================================
st.set_page_config(page_title="AI 金融一键看板", layout="wide")

st.title("📊 AI 金融情报分析系统 (2026版)")
st.caption("集成：实时行情 + 跨品种相关性 + AI 策略诊断")

# 侧边栏配置
st.sidebar.header("系统设置")
selected_tickers = st.sidebar.multiselect("选择分析对象", DEFAULT_TICKERS, default=DEFAULT_TICKERS)

# --- 核心按钮 ---
if st.button("🚀 执行一键同步与 AI 诊断"):
    if not GOOGLE_API_KEY.startswith("AIza"):
        st.error("错误：请先在代码开头的配置区填写有效的 Google API Key（AIza开头）！")
    else:
        with st.spinner("正在抓取实时数据并调动 AI 逻辑，请稍候..."):
            try:
                # 第一步：抓取
                df = fetch_finance_data(selected_tickers)
                
                # 第二步：显示走势图
                st.subheader("📈 资产走势对比 (近30日)")
                # 归一化处理（从 100 开始看涨跌幅，方便对比）
                df_norm = (df / df.iloc[0] * 100)
                st.line_chart(df_norm)
                
                # 第三步：计算并显示相关性矩阵
                st.subheader("📊 跨品种相关性热力图")
                corr = df.corr()
                fig = px.imshow(
                    corr, 
                    text_auto=True, 
                    aspect="auto", 
                    color_continuous_scale='RdBu_r',
                    labels=dict(color="相关性指数")
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # 第四步：AI 诊断
                st.subheader("🤖 AI 深度诊断报告")
                analysis_text = get_ai_analysis(corr)
                st.success(analysis_text)
                
                # 第五步：保存记录（模拟本地存储）
                df.to_csv("last_sync_data.csv")
                st.info(f"数据同步成功。最后更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            except Exception as e:
                st.error(f"运行出错：{e}")

else:
    st.write("👈 请在侧边栏确认品种，然后点击上方按钮开始分析。")
