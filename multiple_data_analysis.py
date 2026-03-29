import pandas as pd
import yfinance as yf
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import streamlit as st
import plotly.express as px
import plotly.figure_factory as ff
import google.generativeai as genai
import os
from dotenv import load_dotenv
import json

# ==========================================
# ⚙️ 核心配置区域
# ==========================================
JSON_KEY_FILE = 'google_key.json' 
SPREADSHEET_ID = '1OtS3I6HbwND5azTXrDP_YJ_ldKgoN-q3dd8g0vtd96I' # 🚨 你的表格ID
WORKSHEET_NAME = 'Raw_Prices'
METADATA_WORKSHEET_NAME = 'Asset_Dict'
AI_MODEL_NAME = 'gemini-3.1-flash-lite-preview' 

# 自动从 .env 文件读取 API Key
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 🛠️ 核心功能函数 (数据抓取与同步)
# ==========================================
@st.cache_resource
def get_gspread_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']   
    # 🌟 核心改动：优先从云端 Secrets 读取
    if "google_KEY" in st.secrets:
        # 这里的 st.secrets["google_KEY"] 就是你填在那个黑框里的内容
        creds_info = json.loads(st.secrets["google_KEY"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
    else:
        # 如果是在本地运行，保留原来的逻辑读取本地文件
        JSON_KEY_FILE = 'google_key.json' 
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEY_FILE, scope)
        
    return gspread.authorize(creds)

def get_google_sheet(client, worksheet_name):
    return client.open_by_key(SPREADSHEET_ID).worksheet(worksheet_name)

def process_yf_data(raw_data, tickers):
    if raw_data.empty: return pd.DataFrame()
    price_col = 'Adj Close' if 'Adj Close' in raw_data.columns else 'Close'
    df = raw_data[price_col].copy()
    if isinstance(df, pd.Series): 
        df = df.to_frame(name=tickers[0])
    df.reset_index(inplace=True)
    df['Date'] = pd.to_datetime(df['Date'], utc=True).dt.strftime('%Y-%m-%d')
    return df

def update_ticker_metadata(client, tickers):
    ss = client.open_by_key(SPREADSHEET_ID)
    try:
        meta_sheet = ss.worksheet(METADATA_WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        meta_sheet = ss.add_worksheet(title=METADATA_WORKSHEET_NAME, rows="100", cols="3")
        meta_sheet.update('A1', [['Ticker', 'Full Name', 'Asset Class']])

    existing_meta = meta_sheet.get_all_records()
    meta_dict = {r['Ticker']: {'Name': r['Full Name'], 'Category': r.get('Asset Class', 'Uncategorized')} for r in existing_meta}
    
    tickers_to_fetch = [t for t in tickers if t not in meta_dict and t != 'Date']
    if not tickers_to_fetch: return meta_dict

    new_meta_rows = []
    progress_bar = st.progress(0, text="正在雅虎抓取新资产信息...")
    
    for i, t in enumerate(tickers_to_fetch):
        try:
            info = yf.Ticker(t).info
            name = info.get('longName', info.get('shortName', t))
            category = info.get('sector', info.get('quoteType', 'Other/Mixed'))
            meta_dict[t] = {'Name': name, 'Category': category}
            new_meta_rows.append([t, name, category])
        except Exception:
            meta_dict[t] = {'Name': t, 'Category': 'Unknown'}
            new_meta_rows.append([t, t, 'Unknown'])
        progress_bar.progress((i + 1) / len(tickers_to_fetch))
        
    if new_meta_rows:
        meta_sheet.append_rows(new_meta_rows)
        progress_bar.empty()
    return meta_dict

def daily_sync():
    client = get_gspread_client()
    sheet = get_google_sheet(client, WORKSHEET_NAME)
    headers = sheet.row_values(1)
    if len(headers) < 2: return st.error("数据库为空！")
    
    existing_tickers = [col for col in headers if col != 'Date']
    update_ticker_metadata(client, existing_tickers)

    df_old = pd.DataFrame(sheet.get_all_records())
    last_date = str(df_old['Date'].max())
    start_date = (datetime.datetime.strptime(last_date, "%Y-%m-%d") - datetime.timedelta(days=4)).strftime('%Y-%m-%d')
    
    new_data_raw = yf.download(existing_tickers, start=start_date, progress=False)
    df_new = process_yf_data(new_data_raw, existing_tickers)
    
    if df_new.empty: return st.warning("暂无新数据同步。")

    df_merged = pd.concat([df_old, df_new], ignore_index=True)
    df_merged.sort_values('Date', inplace=True)
    df_merged.drop_duplicates(subset=['Date'], keep='last', inplace=True)
    df_merged.fillna("", inplace=True)
    
    sheet.clear()
    sheet.update([df_merged.columns.values.tolist()] + df_merged.values.tolist())
    st.success("✅ 同步完成！数据已更新。")

def add_new_assets(tickers_str):
    tickers = [t.strip().upper() for t in tickers_str.split(',') if t.strip()]
    if not tickers: return st.error("请输入资产代码！")

    client = get_gspread_client()
    sheet = get_google_sheet(client, WORKSHEET_NAME)
    headers = sheet.row_values(1)
    existing_tickers = [col for col in headers if col != 'Date'] if headers else []
    
    new_tickers = [t for t in tickers if t not in existing_tickers]
    if not new_tickers: return st.warning("资产已在库中！")

    update_ticker_metadata(client, new_tickers)

    new_data_raw = yf.download(new_tickers, period="max", progress=False)
    df_new = process_yf_data(new_data_raw, new_tickers)
    
    if df_new.empty: return st.error("未能抓取到新资产数据。")

    df_old = pd.DataFrame(sheet.get_all_records()) if existing_tickers else pd.DataFrame(columns=['Date'])
    df_merged = pd.merge(df_old, df_new, on='Date', how='outer')
    df_merged.sort_values('Date', inplace=True)
    df_merged.fillna("", inplace=True)
    
    sheet.clear()
    sheet.update([df_merged.columns.values.tolist()] + df_merged.values.tolist())
    st.success("✅ 新资产添加并入库成功！")

# ==========================================
# 🤖 终极 AI 综合大脑
# ==========================================
def ai_comprehensive_analysis(df_a, df_b, corr_text, asset_names, time_range):
    if not GEMINI_API_KEY: return "⚠️ 未检测到 API Key，请检查根目录下的 .env 文件配置。"
    try:
        model = genai.GenerativeModel(AI_MODEL_NAME)
        prompt = f"""
        你是一位严谨的量化金融分析师，基于数据只讲干货，不要废话，更加不要杜撰。请根据以下【三个维度】的数据，为用户提供一份专业的、直白的干货解读。
        
        【分析标的】: {", ".join(asset_names)}
        【时间段】: {time_range}

        【图表 A：表现摘要 (起点=100归一化)】:
        {df_a.describe().to_string()}

        【图表 B：短线单日涨跌幅摘要 (%)】:
        {df_b.describe().to_string()}

        【图表 C：资产相关性矩阵 (%)】:
        {corr_text}

        请用简体中文简明扼要地总结：
        1. 📈 趋势与表现 (基于图表A)：这段时间谁是收益赢家？谁表现最差？
        2. ⚡ 风险与波动 (基于图表B)：谁的波动最大？谁最稳健？
        3. 🎯 相关性与组合配置 (基于图表C)：哪些标的之间同涨同跌相关性很高（难以对冲）？哪些相关性较低（适合组合优化，对冲风险）？
        4. 基于数据严谨的综合评判。
        """
        return model.generate_content(prompt).text
    except Exception as e:
        return f"❌ AI 调用失败: {str(e)}"

# ==========================================
# 🖥️ 前端 UI 布局
# ==========================================
st.set_page_config(page_title="投研数据中心", layout="wide")
st.title("🔌 投研数据中心 & AI 实验室")

try:
    client = get_gspread_client()
    df_db = pd.DataFrame(get_google_sheet(client, WORKSHEET_NAME).get_all_records())
    df_db.replace("", float("NaN"), inplace=True)
    df_db['Date'] = pd.to_datetime(df_db['Date'], errors='coerce')
    df_db.dropna(subset=['Date'], inplace=True) 
    
    records_meta = get_google_sheet(client, METADATA_WORKSHEET_NAME).get_all_records()
    meta_map = {r['Ticker']: {'Name': r['Full Name'], 'Category': r.get('Asset Class', 'Uncategorized')} for r in records_meta}
    
    all_tickers = [col for col in df_db.columns if col != 'Date']
    available_categories = sorted(list(set([meta_map.get(t, {}).get('Category', 'Unknown') for t in all_tickers])))
    
    # ==========================================
    # 👉 侧边栏：所有控制配置和日常维护全部集中在这里
    # ==========================================
    with st.sidebar:
        st.header("🎯 资产筛选与配置")
        
        # 步骤 1：按分类筛选
        selected_categories = st.multiselect("📁 步骤 1：按分类筛选", options=available_categories)
        filtered_tickers = all_tickers
        if selected_categories:
            filtered_tickers = [t for t in all_tickers if meta_map.get(t, {}).get('Category', 'Unknown') in selected_categories]
        
        # 步骤 2：选择对比资产 (紧跟着步骤1)
        asset_options_formatted = [f"{t} - {meta_map.get(t, {}).get('Name', t)}" for t in filtered_tickers]
        formatted_to_ticker = {f"{t} - {meta_map.get(t, {}).get('Name', t)}": t for t in filtered_tickers}
        ticker_to_formatted = {v: k for k, v in formatted_to_ticker.items()}

        selected_formatted = st.multiselect("🔍 步骤 2：选择对比资产", options=asset_options_formatted)
        selected_tickers = [formatted_to_ticker[f] for f in selected_formatted]
        
        st.markdown("---")
        
        # 数据库维护区
        st.header("🛠️ 数据库维护")
        if st.button("🔄 一键日常同步", use_container_width=True):
            with st.spinner("同步中..."): daily_sync()
        
        st.write("🎯 扩充监控池")
        tickers_input = st.text_input("输入新代码 (逗号分隔):", key="ticker_input_sidebar")
        if st.button("➕ 抓取并入库", use_container_width=True, key="add_asset_sidebar"):
            with st.spinner("抓取中..."): add_new_assets(tickers_input)

    # ==========================================
    # 👉 主区域：只负责干净利落地展示图表和分析
    # ==========================================
    if selected_tickers:
        asset_names_for_ai = [meta_map.get(t, {}).get('Name', t) for t in selected_tickers]
        df_plot = df_db[['Date'] + selected_tickers].copy()
        
        # 主区域第一行：时间滑块
        df_ret_tmp = df_plot.set_index('Date')[selected_tickers].copy()
        df_ret_tmp.ffill(inplace=True) 
        min_date = df_ret_tmp.index.min().date()
        max_date = df_ret_tmp.index.max().date()
        selected_dates = st.slider("🗓️ 拖动滑块框选分析时间段：", min_value=min_date, max_value=max_date, value=(min_date, max_date), format="YYYY-MM-DD", key="time_slider_drag")
        
        start_date_pd, end_date_pd = pd.to_datetime(selected_dates[0]), pd.to_datetime(selected_dates[1])
        time_range_str = f"{selected_dates[0]} 至 {selected_dates[1]}"
        
        df_plot_filtered = df_plot[(df_plot['Date'] >= start_date_pd) & (df_plot['Date'] <= end_date_pd)].copy()
        df_ret_filtered = df_plot_filtered.set_index('Date')[selected_tickers].copy()
        df_ret_filtered.ffill(inplace=True) 
        df_returns_filtered = (df_ret_filtered.pct_change() * 100).reset_index() # (%)
        df_returns_filtered.dropna(subset=['Date'], inplace=True)
        
        # ==========================================
        # 📊 图表 A：长线累计走势
        # ==========================================
        normalize = st.checkbox(
            "🔥 开启起点归一化对比", 
            value=True,
            key="norm_checkbox_clean"
        )
        st.markdown(f"### 📊 图表 A: 长线累计净值对比 (对数坐标系)")

        if normalize:
            for col in selected_tickers:
                first_valid_idx = df_plot_filtered[col].first_valid_index()
                if first_valid_idx is not None:
                    df_plot_filtered[col] = (df_plot_filtered[col] / df_plot_filtered.loc[first_valid_idx, col]) * 100
            y_title_a = "基准100累计净值"
        else:
            y_title_a = "价格"

        fig_line = px.line(df_plot_filtered.rename(columns=ticker_to_formatted), x='Date', y=[ticker_to_formatted[t] for t in selected_tickers])
        
        fig_line.update_layout(
            height=550,
            hovermode="x unified", 
            yaxis_title=y_title_a,
            yaxis_type="log", 
            legend_title="代码"
        )
        if not normalize: fig_line.update_layout(yaxis_tickformat=None)
        
        st.plotly_chart(fig_line, use_container_width=True, key="chart_a_drag")
        st.markdown("---")

        # ==========================================
        # 📈 图表 B：短线图表
        # ==========================================
        st.markdown("### 📈 图表 B: 单日涨跌幅波动散点图")
        fig_scatter = px.scatter(df_returns_filtered.rename(columns=ticker_to_formatted), x='Date', y=[ticker_to_formatted[t] for t in selected_tickers])
        fig_scatter.update_traces(marker_size=3)
        fig_scatter.update_layout(
            height=400, 
            hovermode="x unified", 
            yaxis_title="单日涨跌幅 (%)",
            legend_title="代码",
        )
        st.plotly_chart(fig_scatter, use_container_width=True, key="chart_b_drag")
        st.markdown("---")

        # ==========================================
        # 📊 图表 C：聚类相关性实现 (热力图)
        # ==========================================
        st.markdown("### 📊 图表 C: 资产聚类相关性矩阵 (Heatmap)")
        if len(selected_tickers) >= 2:
            corr_matrix = df_returns_filtered[selected_tickers].corr()
            
            corr_matrix_formatted = corr_matrix.copy()
            formatted_names_list = [meta_map.get(t, {}).get('Name', t) for t in corr_matrix.columns]
            corr_matrix_formatted.columns = formatted_names_list
            corr_matrix_formatted.index = formatted_names_list
            
            fig_heatmap = ff.create_annotated_heatmap(
                z=corr_matrix_formatted.values,
                x=list(corr_matrix_formatted.columns),
                y=list(corr_matrix_formatted.index),
                annotation_text=corr_matrix_formatted.round(3).values,
                colorscale='RdBu_r',
                zmin=-1, zmax=1 
            )
            fig_heatmap.update_layout(
                height=450,
                margin=dict(t=50, b=50, l=120, r=50) 
            )
            st.plotly_chart(fig_heatmap, use_container_width=True, key="chart_corr")
            corr_matrix_text = corr_matrix_formatted.round(3).to_string()
        else:
            st.info("👆 请至少选择两个资产以进行相关性分析。")
            corr_matrix_text = "👆 请至少选择两个资产以进行相关性分析。"

        st.markdown("---")

        # ==========================================
        # 🤖 综合 AI 解读区
        # ==========================================
        st.markdown("### 🧠 综合 AI 投研大脑")
        st.write("结合上方的【表现对比图A】、【单日波动图B】与【聚类热力图C】，一键生成综合诊断报告。")
        
        if st.button("🪄 一键生成综合诊断报告", type="primary", use_container_width=True):
            with st.spinner("AI 正在同时深度分析长线、短线与相关性维度数据..."):
                report = ai_comprehensive_analysis(df_plot_filtered, df_returns_filtered, corr_matrix_text, asset_names_for_ai, time_range_str)
                st.success(report)

    else:
        st.info("👈 请在左侧边栏完成步骤1(分类)和步骤2(资产)的选择。")
except Exception as e:
    st.error(f"❌ 系统发生异常: {e}")
