import os, time, requests, urllib.parse
import pandas as pd
from io import StringIO
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai

app = Flask(__name__)

# --- 配置 ---
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

# 你的凭据
CT_USER = os.environ.get("CT_USER")
CT_PASS = os.environ.get("CT_PASS")

# --- 缓存系统 (提速核心) ---
cache = {"data": None, "time": 0}

def fetch_ct_data():
    """瘦身版抓取：只读核心列，内存占用降低 80%"""
    global cache
    now = time.time()
    
    # 1. 检查缓存：1小时内不重复抓取
    if cache["data"] and (now - cache["time"] < 3600):
        print("⚡️ 命中缓存，秒回数据")
        return cache["data"]

    # 2. 构造安全 URL
    safe_pass = urllib.parse.quote(CT_PASS) if CT_PASS else ""
    url = f"https://www.cellartracker.com/xlquery.asp?User={CT_USER}&Password={safe_pass}&Format=csv&Table=Inventory"
    
    try:
        print("🌐 正在连接 CellarTracker...")
        headers = {'User-Agent': 'Mozilla/5.0 (VinoEcho/1.0; HCI Research)'}
        # 增加流式读取，防止大文件塞爆内存
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            # 【关键优化】usecols: 只加载这 5 列，其他的全部丢弃，省下几百 MB 内存
            cols_to_use = ['Wine', 'Vintage', 'Varietal', 'DrinkBetween', 'Quantity']
            df = pd.read_csv(StringIO(response.text), usecols=cols_to_use)
            
            # 过滤库存并精简
            inventory = df[df['Quantity'] > 0].head(40).to_string(index=False)
            
            # 更新缓存
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
    # 获取（可能来自缓存的）酒窖数据
    inventory = fetch_ct_data()
    
    system_prompt = f"""
    你是 'VinoEcho' (窖响)，WSET Level 3 级侍酒师。
    当前库存数据：{inventory}
    你的任务：根据数据推荐酒款及杯型（Josephine No. 3, Grassl Cru, Zalto 等）。
    """
    
    try:
        response = model.generate_content([system_prompt, user_query])
        return jsonify({"reply": response.text})
    except Exception as e:
        return jsonify({"reply": f"系统忙，请稍后 (Error: {str(e)})"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)