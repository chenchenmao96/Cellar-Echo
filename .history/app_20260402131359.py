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
    
    # 1. 获取长期偏好摘要 + 最近对话历史
    long_term_summary = cellar_db.get_user_summary(user_id)
    recent_history = cellar_db.get_recent_history(user_id, limit=8)
    inventory = cellar_db.get_inventory_for_ai(user_id)
    
    # 2. 构造 System Prompt（融入长期记忆）
    system_prompt = f"""
    你是 'VinoEcho'，WSET Level 3 级侍酒师。
    【陈陈的长期画像】：{long_term_summary}
    【当前酒窖清单】：{inventory}
    请结合长期画像和近期对话提供专业建议。
    """

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(recent_history)
    messages.append({"role": "user", "content": user_query})

    try:
        # 3. 发送请求
        response = client.chat.completions.create(model="deepseek-chat", messages=messages)
        reply = response.choices[0].message.content
        
        # 4. 存入新对话并检查历史长度
        cellar_db.save_chat_and_check_limit(user_id, "user", user_query)
        total_count = cellar_db.save_chat_and_check_limit(user_id, "assistant", reply)
        
        # 5. 【核心自动化】如果对话堆积超过 20 条，开始“瘦身”
        if total_count > 20:
            old_text = cellar_db.get_old_messages_for_summary(user_id, limit=12)
            # 调用 AI 生成新摘要
            summary_prompt = f"请根据以下对话内容，更新用户的长期偏好画像（包括酒款喜好、杯型习惯等）。原画像：{long_term_summary}\n新对话：{old_text}"
            summary_res = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": summary_prompt}]
            )
            cellar_db.update_user_summary(user_id, summary_res.choices[0].message.content)

        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": f"Error: {str(e)}"}), 500
if __name__ == '__main__':
    # 把 port 改成 5001
    app.run(host='0.0.0.0', port=5001, debug=True)