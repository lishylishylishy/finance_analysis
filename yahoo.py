import os
import pandas as pd
import yfinance as yf
import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
import plotly.express as px

# ====================================================================
# 🔐 --- 核心初始化 --- 🔐
# ====================================================================
load_dotenv() 
API_KEY = os.getenv("GEMINI_API_KEY")

# ====================================================================
# 🛠️ --- 用户专属配置区域 --- 🛠️
# ====================================================================
PAGE_TITLE = "全资产相关性研报"
PAGE_LAYOUT = "wide"
APP_TITLE = "📈 跨资产行情与 AI 相关性分析助手"

DEFAULT_SINGLE_TICKER = "WCP.TO"

CORRELATION_TICKERS_INPUT = (
    "DX-Y.NYB, CNY=X, JPY=X, "
    "GC=F, XAUUSD=X, 518880.SS, SI=F, XAGUSD=X, 512400.SS, CL=F, BZ=F, USO, HG=F, 501018.SH, XME, "
    "QQQ, ^N225, ^KS11, ^TA125.TA, ^ITECH, "
    "601899.SS, 000603.SZ, 600362.SS, "
    "600900.SS, 601088.SS, WCP.TO, 601886.SS, "
    "TSLA, GOOGL, MSFT, NVDA, TSM, ITA, LMT, TEVA, "
    "BTC-USD"
)

CORRELATION_PERIOD = "1mo"
CORRELATION_INTERVAL = "1d" 
AI_MODEL_NAME = "gemini-3.1-flash-lite-preview"

# ====================================================================
# --- 底层核心逻辑代码 ---
# ====================================================================

st.set_page_config(page_title=PAGE_TITLE, layout=PAGE_LAYOUT)
st.title(APP_TITLE)

if not API_KEY:
    st.sidebar.error("⚠️ 未检测到 API Key，请配置环境变量。")
else:
    st.sidebar.success("✅ AI 引擎已连接")

@st.cache_data(ttl=3600)
def download_data_for_ticker(ticker, period="1mo", interval="1d"):
    try:
        data = yf.download(ticker, period=period, interval=interval, progress=False)
        return data if not data.empty else None
    except:
        return None

@st.cache_data(ttl=3600)
def calculate_financial_correlation(tickers_str, period, interval):
    clean_str = tickers_str.replace('\n', ' ').replace('\r', '')
    tickers = [t.strip().upper() for t in clean_str.split(',') if t.strip()]
    
    if len(tickers) < 2: return None, None
    
    progress_text = "🚀 正在并发批量下载资产行情数据..."
    my_bar = st.progress(0, text=progress_text)
    
    try:
        # 核心优化：使用 yf 批量下载，避开单个循环请求导致雅虎拉黑 IP
        data = yf.download(tickers, period=period, interval=interval, progress=False)
        
        if data.empty:
            my_bar.empty()
            return None, None
            
        # 智能提取收盘价
        if 'Adj Close' in data.columns.levels[0]:
            prices = data['Adj Close']
        elif 'Close' in data.columns.levels[0]:
            prices = data['Close']
        else:
            my_bar.empty()
            return None, None
            
        my_bar.progress(1.0, text="✅ 数据下载完成，正在计算相关性矩阵...")
        
    except Exception as e:
        my_bar.empty()
        st.error(f"批量下载数据失败: {e}")
        return None, None
        
    my_bar.empty()
    
    # 计算收益率和相关性
    returns = prices.dropna(how='all').pct_change().dropna(how='all')
    corr_matrix = returns.corr()
    
    # 清理全是 NaN 的行列
    corr_matrix = corr_matrix.dropna(axis=0, how='all').dropna(axis=1, how='all')
    
    return corr_matrix, list(corr_matrix.columns)

def generate_ai_analysis(matrix_df, tickers):
    try:
        genai.configure(api_key=API_KEY)
        model = genai.GenerativeModel(AI_MODEL_NAME)
        matrix_markdown = matrix_df.to_markdown()
        
        prompt = f"""你是严谨的量化分析师。以下是资产收益率相关性矩阵：
{matrix_markdown}
分析核心标的：【{DEFAULT_SINGLE_TICKER}】。
1. 格式强制：提及资产必须采用代码格式。
2. 数值锚定：必须带上相关性系数。
请严格输出分析报告。"""
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"❌ AI 分析失败: {str(e)}"

# ====================================================================
# --- 网页 UI ---
# ====================================================================
st.sidebar.header("⚙️ 参数配置")
correlation_tickers_sidebar = st.sidebar.text_area("分析清单", value=CORRELATION_TICKERS_INPUT, height=250)
correlation_period_sidebar = st.sidebar.text_input("时间范围", value=CORRELATION_PERIOD)
correlation_interval_sidebar = st.sidebar.selectbox("数据粒度", options=["1d", "1h", "15m", "5m"])

if st.sidebar.button("🚀 开始跨资产深度分析"):
    if correlation_tickers_sidebar:
        with st.spinner('数据光速拉取中，请稍候...'):
            corr_matrix, actual_tickers = calculate_financial_correlation(
                correlation_tickers_sidebar, 
                correlation_period_sidebar, 
                correlation_interval_sidebar
            )
            
        if corr_matrix is not None and not corr_matrix.empty:
            st.subheader("📊 资产收益率相关性矩阵")
            styled_matrix = corr_matrix.style.background_gradient(cmap='coolwarm', vmin=-1, vmax=1).format("{:.2f}")
            st.dataframe(styled_matrix, height=550)
            
            st.markdown("---")
            st.subheader("🤖 AI 量化速递")
            with st.spinner('🧠 AI 正在根据矩阵撰写研报...'):
                ai_report = generate_ai_analysis(corr_matrix, actual_tickers)
                st.info(ai_report)
        else:
            st.error("未能获取到有效的行情数据，请检查资产代码或网络连接。")

# ====================================================================
# 🎯 --- 单一资产走势图 ---
# ====================================================================
st.markdown("---")
with st.expander("🔍 单一资产走势快速核查"):
    ticker_q = st.text_input("输入代码", value=DEFAULT_SINGLE_TICKER)
    if ticker_q:
        with st.spinner(f"正在获取 {ticker_q} 的历史走势..."):
            q_data = download_data_for_ticker(
                ticker_q, 
                period=CORRELATION_PERIOD, 
                interval=CORRELATION_INTERVAL
            )
        
        if q_data is not None and not q_data.empty:
            try:
                # 适配新版 yfinance 多层索引返回
                price_col = 'Adj Close' if 'Adj Close' in q_data.columns else 'Close'
                plot_df = q_data[price_col].copy()
                
                if isinstance(plot_df, pd.DataFrame):
                    plot_df = plot_df.iloc[:, 0]
                
                df_reset = plot_df.reset_index()
                df_reset.columns = ['Date', 'Price']
                df_reset['Date'] = pd.to_datetime(df_reset['Date']).dt.tz_localize(None)
                
                dynamic_title = f"{ticker_q} 历史走势 ({CORRELATION_PERIOD})"
                
                fig = px.line(df_reset, x='Date', y='Price', title=dynamic_title)
                fig.update_yaxes(autorange=True, fixedrange=False)
                fig.update_layout(margin=dict(l=0, r=0, t=30, b=0), height=400)
                
                st.plotly_chart(fig, use_container_width=True)
                
            except Exception as e:
                st.error(f"绘图逻辑出错: {e}")
        else:
            st.error(f"❌ 无法获取 {ticker_q} 的行情数据。")