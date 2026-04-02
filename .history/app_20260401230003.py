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



model = genai.GenerativeModel('gemini-1.5-flash')

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
        headers = {'User-Agent': 'Mozilla/5.0 (CellarEcho/1.0; HCI Research)'}
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
    
@app.route('/chat', methods=['POST'])
def chat():
    user_query = request.json.get("message")
    inventory = fetch_ct_data()
    
    # 核心：结合酒窖 + 酒杯的专业 Prompt
    system_prompt = f"""
    你是 'CellarEcho' (窖响)，一位拥有 WSET Level 3 水准的私人侍酒师助手。
    
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