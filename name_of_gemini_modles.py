import os
import google.generativeai as genai
from dotenv import load_dotenv

# 加载你的环境变量（确保 .env 文件里有 GEMINI_API_KEY）
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

print("👇 你的 API Key 支持的可用模型真实代码如下：")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)