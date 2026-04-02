# app.py 修正版核心部分
import os, requests, pandas as pd
from io import StringIO
from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from database import cellar_db # 👈 必须导入
import time
import threading  # 👈 必须导入线程库
from flask import Response
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

# app.py 新增逻辑
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    
    # 尝试连接 CT 进行验证
    test_url = f"https://www.cellartracker.com/xlquery.asp?User={username}&Password={password}&Format=csv&Table=Inventory"
    try:
        # 只取头部数据，验证是否能跑通
        res = requests.get(test_url, timeout=15)
        if res.status_code == 200 and "Wine" in res.text:
            return jsonify({"status": "success", "user_id": username})
        return jsonify({"status": "error", "message": "CT 账号或密码错误"}), 401
    except Exception as e:
        return jsonify({"status": "error", "message": "无法连接到 CellarTracker"}), 500

@app.route('/update_cellar', methods=['POST'])
def update_cellar():
    data = request.json
    user_id = data.get("user_id")
    ct_pass = data.get("password") # 👈 从前端接收用户刚才登录的密码
    
    url = f"https://www.cellartracker.com/xlquery.asp?User={user_id}&Password={ct_pass}&Format=csv&Table=Inventory"
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


def run_summary_in_background(user_id, long_term_summary, old_text):
    """后台静默处理摘要，不让用户在前端转圈圈"""
    try:
        summary_prompt = f"请根据以下对话内容，更新用户的长期偏好画像。原画像：{long_term_summary}\n新对话：{old_text}"
        summary_res = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": summary_prompt}]
        )
        cellar_db.update_user_summary(user_id, summary_res.choices[0].message.content)
    except Exception as e:
        print(f"⚠️ 后台摘要生成失败: {e}")

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_id = data.get("user_id", "Chenchen")
    user_query = data.get("message")
    
    # 获取上下文和酒窖数据
    long_term_summary = cellar_db.get_user_summary(user_id)
    recent_history = cellar_db.get_recent_history(user_id, limit=8)
    inventory = cellar_db.get_inventory_for_ai(user_id, limit=30) # 👈 缩小数据量也能提速

    messages = [{"role": "system", "content": f"你是 'VinoEcho' 侍酒师。已知偏好：{long_term_summary}\n库存：{inventory}"}]
    messages.extend(recent_history)
    messages.append({"role": "user", "content": user_query})

    def generate():
        full_reply = ""
        try:
            # 1. 开启流式调用
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                stream=True # 👈 开启流式开关
            )
            
            for chunk in response:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_reply += content
                    yield content # 👈 实时推送到浏览器

            # 2. 只有在流式传输结束后，才处理数据库存取
            cellar_db.save_chat_and_check_limit(user_id, "user", user_query)
            total_count = cellar_db.save_chat_and_check_limit(user_id, "assistant", full_reply)
            
            # 3. 触发异步摘要逻辑
            if total_count > 20:
                old_text = cellar_db.get_old_messages_for_summary(user_id, limit=12)
                # 开启新线程去调 API，主线程直接结束，用户不需要等待
                threading.Thread(
                    target=run_summary_in_background, 
                    args=(user_id, long_term_summary, old_text)
                ).start()
                
        except Exception as e:
            yield f"Error: {str(e)}"

    # 4. 使用 Response 返回生成器内容
    return Response(generate(), mimetype='text/plain')
if __name__ == '__main__':
    # 把 port 改成 5001
    app.run(host='0.0.0.0', port=5001, debug=True)