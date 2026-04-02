import os
import requests
import pandas as pd
from io import StringIO
from flask import Flask, request, jsonify, render_template  # 必须加上 render_template
import google.generativeai as genai

app = Flask(__name__)

@app.route('/')
def index():
    # 这行代码会告诉 Flask 去 templates 文件夹找 index.html 并展示出来
    return render_template('index.html')

# --- 配置区 ---
# 建议在 Render 的 Environment Variables 中设置这些值
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
CT_USER = os.environ.get("CT_USER")
CT_PASS = os.environ.get("CT_PASS")

MY_GLASSWARE = os.environ.get(
    "MY_GLASSWARE", 
    "Zalto Universal, Josephine No. 3, Zalto Bordeaux, Grassl Cru, Gabriel-Glas Gold Edition"
)



model = genai.GenerativeModel('gemini-3-flash-preview')

import urllib.parse

def fetch_ct_data():
    """带调试功能的 CellarTracker 抓取逻辑"""
    # 1. 确保账号密码经过了 URL 编码
    
    url = f"https://www.cellartracker.com/xlquery.asp?User={CT_USER}&Format=csv&Table=List"
    try:
        # 添加 User-Agent 模拟真实浏览器访问
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            # 检查返回内容是否包含“未登录”提示（有时即使公开，接口也会抽风）
            if "not logged into" in response.text.lower():
                return "CellarTracker 接口提示：数据未公开或需要登录。"
            
            # 使用 pandas 处理 CSV 数据
            df = pd.read_csv(StringIO(response.text))
            
            # 只筛选你真正拥有的酒 (Quantity > 0)
            if 'Quantity' in df.columns:
                df = df[df['Quantity'] > 0]
            
            # 筛选对 AI 建议最有用的核心列
            cols = ['Wine', 'Vintage', 'Varietal', 'DrinkBetween', 'Score', 'Quantity']
            # 确保这些列在数据中确实存在
            available_cols = [c for c in cols if c in df.columns]
            
            return df[available_cols].head(100).to_string(index=False)
        else:
            return f"抓取失败，错误码: {response.status_code}"
            
    except Exception as e:
        return f"系统异常: {str(e)}"
    
@app.route('/chat', methods=['POST'])
def chat():
    user_query = request.json.get("message")
    inventory = fetch_ct_data()
    
    # 核心：结合酒窖 + 酒杯的专业 Prompt
    system_prompt = f"""
    你是 'VinoEcho' (窖响)，一位拥有 WSET Level 3 水准的私人侍酒师助手。
    
    【你的任务】
    1. 基于用户的酒窖数据回答问题：{inventory}
    2. 用户目前拥有的酒杯清单：{MY_GLASSWARE}
    3. 每次推荐酒款时，必须从上面的【酒杯清单】中选出最合适的一款，并简述原因（例如：'为了更好地展现 Pinot Noir 的精细香气，建议使用 Grassl Cru'）。
    
    【语气要求】
    专业、优雅、简洁。如果用户问“现在喝什么”，请参考 DrinkBetween 年份。
    """
    
    try:
        # 合并上下文发送给 Gemini
        response = model.generate_content([system_prompt, user_query])
        return jsonify({"reply": response.text})
    except Exception as e:
        return jsonify({"reply": f"抱歉，发生了错误：{str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)