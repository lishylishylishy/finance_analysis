import streamlit as st
import requests, json, re, os, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
import concurrent.futures

# =================================================
# ⚙️ 配置与初始化 (用户修改区)
# =================================================
load_dotenv()

# --- API 核心配置 ---
API_KEY = os.getenv("QWEN_API_KEY", "")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3-vl-235b-a22b-thinking" 
API_TIMEOUT = 120
MAX_WORKERS = 5

# --- 界面默认参数配置 ---
DEFAULT_KEYWORD = "黄金"
DEFAULT_SCOPE = "domestic"
DEFAULT_COUNT = 20
DEFAULT_HOURS = 72

st.set_page_config(page_title="AI 舆情分析看板", layout="wide")

if not API_KEY:
    st.error("⚠️ 未检测到 QWEN_API_KEY。请在 .env 文件或系统环境变量中进行配置。")
    st.stop()

# =================================================
# 🧰 独立工具函数区
# =================================================
@st.cache_data(ttl=3600)
def ai_translate_keyword(keyword):
    if all(ord(c) < 128 for c in keyword): return keyword
    try:
        r = requests.post(f"{BASE_URL}/chat/completions", 
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": MODEL, "messages": [{"role": "user", "content": f"Translate '{keyword}' to a concise English news search term. Return ONLY the translation."}]}, timeout=10)
        content = r.json()['choices'][0]['message']['content']
        # 【核心修复】：剔除 AI 翻译时的 <think> 思考过程
        content_no_think = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip().strip('"')
        return content_no_think
    except: 
        return keyword

def clean_and_parse_json(content):
    content_no_think = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    json_match = re.search(r'\{.*\}', content_no_think, re.S)
    if not json_match:
        raise ValueError("无法从 AI 回复中提取到 JSON 结构。")
    return json.loads(json_match.group())

# =================================================
# 🧠 核心业务类
# =================================================
class NewsAnalyzerCloud:
    def __init__(self, keyword, scope, max_count, hours):
        self.keyword = keyword
        self.scope = scope
        self.max_count = max_count
        self.hours = hours
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})
        self.articles = []
        self.candidate_count = 0 

    def _check_single_source(self, art):
        prompt = f"判断媒体 '{art['source']}' 是否属于中国(含港澳台)。只回'是'或'否'。"
        try:
            r = requests.post(f"{BASE_URL}/chat/completions", 
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]}, timeout=10)
            content = r.json()['choices'][0]['message']['content']
            # 【核心修复】：剔除判定媒体时的思考过程，防止"是"字误判
            content_no_think = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            is_cn = "是" in content_no_think
            
            if (self.scope == "domestic" and is_cn) or (self.scope == "international" and not is_cn):
                return art
        except:
            return art  
        return None

    def fetch(self):
        search_kw = ai_translate_keyword(self.keyword) if self.scope == "international" else self.keyword
        # 【核心修复】：将国际新闻的 gl 节点由 WW 改为 US，大幅提升英文新闻获取率
        url = f"https://news.google.com/rss/search?q=intitle:{search_kw}&hl={'zh-CN' if self.scope=='domestic' else 'en-US'}&gl={'CN' if self.scope=='domestic' else 'US'}&ceid={'CN:zh-Hans' if self.scope=='domestic' else 'US:en'}"
        
        try:
            resp = self.session.get(url, timeout=20)
            resp.raise_for_status()
            items = ET.fromstring(resp.content).findall('.//item')
            
            candidates = []
            for item in items:
                pub = item.findtext('pubDate', '')
                if not pub: continue
                
                dt = parsedate_to_datetime(pub)
                if (datetime.now(timezone.utc) - dt).total_seconds() / 3600 > self.hours: continue

                link = item.findtext('link', '')
                candidates.append({
                    "title": item.findtext('title', '').split(' - ')[0],
                    "url": link, 
                    "source": item.findtext('source', '') or urlsplit(link).netloc, 
                    "pub": pub
                })
                if len(candidates) >= self.max_count * 2: break
            
            self.candidate_count = len(candidates) 
            
            valid = []
            progress_bar = st.progress(0, text="AI 正在并发验证地域属性...")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(self._check_single_source, art): art for art in candidates}
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    result = future.result()
                    if result: valid.append(result)
                    progress_bar.progress((i+1)/len(candidates), text=f"核查进度: {i+1}/{len(candidates)}")
                    if len(valid) >= self.max_count: break

            progress_bar.empty()
            self.articles = valid[:self.max_count]
            
        except Exception as e:
            st.error(f"❌ 抓取失败: {e}")

    def get_analysis(self):
        if not self.articles: return None
        
        context = "\n".join([f"[{a['source']}] {a['title']}" for a in self.articles[:10]])
        prompt = f"""你是专业的新闻舆情分析师。请严格基于以下新闻列表返回 JSON 格式数据。
必须包含且仅包含以下 6 个字段：
1. summary (字符串，300字以内的全局事件总结)
2. core_keywords (字符串数组，5个核心关键词)
3. sentiment_data (字典：包含"积极", "中性", "消极"的比例数值，总和为1)
4. sentiment_explanation (字符串，一句话解释情绪图表分析了什么对象，反映了什么基调)
5. topic_data (字典：2-3个核心议题及其比例数值，总和为1)
6. topic_explanation (字符串，一句话解释议题分布反映了媒体关注的重心)

禁止返回Markdown代码块标识，只返回纯JSON。
数据：\n{context}"""
        
        try:
            r = requests.post(f"{BASE_URL}/chat/completions", 
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]}, timeout=API_TIMEOUT)
            r.raise_for_status()
            
            content = r.json()['choices'][0]['message']['content']
            return clean_and_parse_json(content)
            
        except Exception as e:
            st.error(f"❌ 深度分析解析失败: {e}")
            return None

# =================================================
# 🖥️ Streamlit 前端渲染
# =================================================
st.sidebar.header("🔍 检索配置")
kw = st.sidebar.text_input("关键词", value=DEFAULT_KEYWORD)
scope = st.sidebar.selectbox("范围", options=["domestic", "international"], 
                             index=0 if DEFAULT_SCOPE == "domestic" else 1,
                             format_func=lambda x: "国内新闻" if x=="domestic" else "国际新闻")
count = st.sidebar.slider("抓取数量", 5, 50, DEFAULT_COUNT)
hours = st.sidebar.number_input("时效(小时)", value=DEFAULT_HOURS)

if st.sidebar.button("🚀 开始检索并分析"):
    analyzer = NewsAnalyzerCloud(kw, scope, count, hours)
    
    with st.status("正在执行深度分析...", expanded=True) as status:
        st.write("🌐 正在并发连接与核查...")
        analyzer.fetch()
        
        actual_count = len(analyzer.articles)
        candidate_count = analyzer.candidate_count
        scope_name = "国内新闻" if scope == "domestic" else "国际新闻"
        
        if actual_count == 0:
            st.error("❌ 未找到符合条件的新闻。请尝试扩大时效范围或更换关键词。")
            st.stop()
        elif actual_count < count:
            st.warning(f"⚠️ **抓取说明**：目标设定 **{count}** 篇。底层引擎共寻获 **{candidate_count}** 篇初筛报道，经 AI 严格剔除非【{scope_name}】媒体后，最终保留 **{actual_count}** 篇投入深度分析。")
        else:
            st.success(f"✅ **抓取说明**：底层引擎共寻获 **{candidate_count}** 篇初筛报道，经 AI 过滤后，成功足额提取了 **{actual_count}** 篇有效新闻投入深度分析。")
            
        st.write("🧠 AI 正在生成研报与图表解读...")
        report = analyzer.get_analysis()
        status.update(label="分析完成！", state="complete", expanded=False)

    if report:
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.subheader("📊 情绪分布")
            sentiment_data = report.get('sentiment_data', {})
            if sentiment_data:
                sent_df = pd.DataFrame(list(sentiment_data.items()), columns=['情绪', '比例'])
                color_map = {"中性": "#4285F4", "消极": "#9E9E9E", "积极": "#FF9800"} 
                fig_bar = px.bar(sent_df, x='情绪', y='比例', color='情绪', color_discrete_map=color_map)
                fig_bar.update_layout(showlegend=False, xaxis_title=None, yaxis_title="占比")
                st.plotly_chart(fig_bar, use_container_width=True)
            st.info(f"💡 **图表解读**：{report.get('sentiment_explanation', '暂无解读')}")

        with col2:
            st.subheader("📈 核心议题")
            topic_data = report.get('topic_data', {})
            if topic_data:
                topic_df = pd.DataFrame(list(topic_data.items()), columns=['议题', '比例'])
                fig_pie = px.pie(topic_df, values='比例', names='议题', hole=0.3)
                fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                fig_pie.update_layout(showlegend=False)
                st.plotly_chart(fig_pie, use_container_width=True)
                st.info(f"💡 **图表解读**：{report.get('topic_explanation', '暂无解读')}")
            else:
                st.write("暂无议题数据")

        st.divider()
        
        st.subheader("🔑 核心关键词")
        keywords = report.get('core_keywords', [])
        st.markdown(" ".join([f"`{kw}`" for kw in keywords]))
        
        st.write("") 

        st.subheader("📝 舆情全局总结")
        st.success(report.get('summary', '无总结生成'))

        st.subheader("📰 新闻源清单")
        df_articles = pd.DataFrame(analyzer.articles)[['source', 'title', 'pub']]
        df_articles.columns = ['媒体来源', '新闻标题', '发布时间'] 
        df_articles.index = df_articles.index + 1
        st.table(df_articles)