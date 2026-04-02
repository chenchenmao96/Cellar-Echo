import os
import time
import requests
import urllib.parse
import pandas as pd
from io import StringIO
from flask import Flask, request, jsonify, render_template
from openai import OpenAI  # 👈 换成这个，DeepSeek 完美兼容
from dotenv import load_dotenv



app = Flask(__name__)

# --- 核心配置：切换到 DeepSeek ---
# 注意：base_url 必须指向 deepseek
client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_query = data.get("message")
    lang = data.get("lang", "zh") # 获取前端传来的语言偏好
    
    inventory = fetch_ct_data()
    
    # 语言指令
    lang_instruction = "Please reply in English." if lang == "en" else "请使用中文回复。"
    
    system_prompt = f"""
    你是 'CellarEcho' (窖响)，WSET Level 3 级侍酒师。{lang_instruction}
    当前库存数据：{inventory}
    可用酒杯：{MY_GLASSWARE}
    请根据适饮期推荐酒款并匹配酒杯，保持专业且优雅。
    """
    
    try:
        # 👈 换成 DeepSeek 的标准调用格式
        response = client.chat.completions.create(
            model="deepseek-chat",  # 对应 DeepSeek-V3
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ],
            stream=False
        )
        return jsonify({"reply": response.choices[0].message.content})
    except Exception as e:
        # HCI 友好的错误提示
        return jsonify({"reply": f"侍酒师去地窖取酒了，稍等... (Error: {str(e)})"}), 500