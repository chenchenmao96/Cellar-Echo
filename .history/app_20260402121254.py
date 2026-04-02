# app.py 修正版核心部分
import os, requests, pandas as pd
from io import StringIO
from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from database import cellar_db # 👈 必须导入


app = Flask(__name__)

# 配置 DeepSeek
client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

# 你的杯型偏好
MY_GLASSWARE = "Zalto Universal, Josephine No. 3, Zalto Bordeaux, Grassl Cru"

@app.route('/')
def index():
    return render_template('index.html')

# 【新增】处理前端同步请求的路由
@app.route('/update_cellar', methods=['POST'])
def update_cellar():
    user_id = request.json.get("user_id", "Chenchen")
    ct_user = os.environ.get("CT_USER")
    ct_pass = os.environ.get("CT_PASS")
    
    url = f"https://www.cellartracker.com/xlquery.asp?User={ct_user}&Password={ct_pass}&Format=csv&Table=Inventory"
    
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            df = pd.read_csv(StringIO(response.text))
            # 过滤掉库存为 0 的，转为字典列表
            wine_data = df[df['QuantityCommunity'] > 0].to_dict('records')
            # 调用数据库模块同步
            count = cellar_db.sync_inventory(user_id, wine_data)
            return jsonify({"status": "success", "count": count})
        return jsonify({"status": "error", "message": "CT 返回错误"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_id = data.get("user_id", "Chenchen")
    user_query = data.get("message")
    lang = data.get("lang", "zh")
    
    # 【核心修改】从数据库读取全量信息，不再使用 fetch_ct_data()
    inventory = cellar_db.get_inventory_for_ai(user_id)
    
    lang_instruction = "Please reply in English." if lang == "en" else "请使用中文回复。"
    
    system_prompt = f"""
    你是 'CellarEcho'，WSET Level 3 级侍酒师。{lang_instruction}
    当前酒窖全量数据：{inventory}
    可用酒杯：{MY_GLASSWARE}
    请根据这些信息（特别是专业评分和适饮期）提供精准建议。
    """
    # ... 剩下的 DeepSeek 调用逻辑保持不变 ...
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


if __name__ == '__main__':
    # 把 port 改成 5001
    app.run(host='0.0.0.0', port=5001, debug=True)