import os
import time
import requests
import pandas as pd
import urllib.parse
from io import StringIO
from flask import Flask, request, jsonify, render_template
# 使用 2026 年最新的官方库
from google import genai

app = Flask(__name__)

# --- 核心配置 ---
# 初始化新版 Client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

CT_USER = os.environ.get("CT_USER")
CT_PASS = os.environ.get("CT_PASS")
MY_GLASSWARE = os.environ.get(
    "MY_GLASSWARE", 
    "Zalto Universal, Josephine No. 3, Zalto Bordeaux, Grassl Cru, Gabriel-Glas Gold Edition"
)

# 缓存系统
cache = {"data": None, "time": 0}

def fetch_ct_data():
    """瘦身版抓取：只读核心列，极致节省内存"""
    global cache
    now = time.time()
    
    if cache["data"] and (now - cache["time"] < 3600):
        return cache["data"]

    safe_pass = urllib.parse.quote(CT_PASS) if CT_PASS else ""
    url = f"https://www.cellartracker.com/xlquery.asp?User={CT_USER}&Password={safe_pass}&Format=csv&Table=Inventory"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (CellarEcho/1.0; HCI Research)'}
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            # 只加载 5 列，避免内存溢出
            cols_to_use = ['Wine', 'Vintage', 'Varietal', 'DrinkBetween', 'Quantity']
            df = pd.read_csv(StringIO(response.text), usecols=cols_to_use)
            
            inventory = df[df['Quantity'] > 0].head(40).to_string(index=False)
            cache["data"] = inventory
            cache["time"] = now
            return inventory
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return cache["data"] if cache["data"] else "同步中..."

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    user_query = request.json.get("message")
    inventory = fetch_ct_data()
    
    system_prompt = f"""
    你是 'CellarEcho' (窖响)，WSET Level 3 级侍酒师。
    当前库存数据：{inventory}
    可用酒杯：{MY_GLASSWARE}
    请根据适饮期推荐酒款并匹配酒杯，保持专业且优雅。
    """
    
    try:
        # 使用新版 API 调用方式
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=[system_prompt, user_query]
        )
        return jsonify({"reply": response.text})
    except Exception as e:
        return jsonify({"reply": f"侍酒师正在忙着洗杯子 (Error: {str(e)})"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)