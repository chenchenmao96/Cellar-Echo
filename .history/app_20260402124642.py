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
    
    # 1. 获取最近的聊天历史
    history = cellar_db.get_chat_history(user_id)
    
    # 2. 获取精简后的酒窖清单 (只发前 50 瓶高分酒)
    inventory = cellar_db.get_inventory_for_ai(user_id, limit=50)
    
    system_prompt = f"""
    你是 'VinoEcho'，WSET Level 3 级侍酒师。
    当前酒窖高分精选：{inventory}
    请结合上下文和酒窖数据提供建议。
    """

    # 3. 构建消息列表（System + History + Current User Query）
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_query})

    try:
        response = client.chat.completions.create(
            model="deepseek-chat", # 👈 确保使用 V3 提高速度
            messages=messages,
            stream=False # 如果要流式回复，前端也需要大改，暂设为 False
        )
        
        reply = response.choices[0].message.content
        
        # 4. 【关键】把这次对话存入数据库
        cellar_db.save_chat(user_id, "user", user_query)
        cellar_db.save_chat(user_id, "assistant", reply)
        
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": f"Error: {str(e)}"}), 500

if __name__ == '__main__':
    # 把 port 改成 5001
    app.run(host='0.0.0.0', port=5001, debug=True)