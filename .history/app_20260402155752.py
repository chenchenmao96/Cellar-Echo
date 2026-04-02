# app.py 修正版核心部分
import os, requests, pandas as pd
from io import StringIO
from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from database import cellar_db # 👈 必须导入
import time
import threading  # 👈 必须导入线程库
from flask import Response
import json
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
    password = data.get("password")
    username = data.get("username").lower() # 👈 强制小写
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
    user_id = data.get("user_id").lower()
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
    """
    在后台像私人管家一样整理记忆。
    任务：识别昵称变更、酒杯更新、偏好沉淀。
    """
    try:
        # 先读取当前数据库里的旧信息
        user_info = cellar_db.get_user_summary(user_id)
        curr_nickname = user_info.get("nickname", "未设置")
        curr_glassware = user_info.get("glassware", "未记录")

        # 核心 Prompt：让 AI 扮演档案管理员
        task_prompt = f"""
        你是 CellarEcho 的记忆管理员。基于以下对话，更新用户档案：
        
        【当前档案】：
        - 昵称：{curr_nickname}
        - 酒杯：{curr_glassware}
        - 长期偏好：{long_term_summary}
        
        【任务说明】：
        1. 检查对话中用户是否要求更改称呼（如“叫我...”、“别叫我...了”）。
        2. 检查酒杯是否有变动。如果是新买的，加入列表；如果是替换，则更新列表。
        3. 提炼新的红酒偏好（如：更喜欢 Pinot Noir 了）。
        
        【最近对话】：
        {old_text}
        
        请严格按 JSON 返回：{{"nickname": "最新昵称", "glassware": "完整酒杯列表", "summary": "更新后的摘要"}}
        """

        res = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": task_prompt}],
            response_format={ "type": "json_object" }
        )
        
        import json
        result = json.loads(res.choices[0].message.content)

        # 1. 如果昵称有变，更新数据库
        if result.get("nickname") and result["nickname"] != curr_nickname:
            cellar_db.update_nickname(user_id, result["nickname"])
            print(f"👤 昵称已自动更新为: {result['nickname']}")

        # 2. 如果酒杯清单有变，更新数据库
        if result.get("glassware") and result["glassware"] != curr_glassware:
            cellar_db.update_glassware(user_id, result["glassware"])
            print(f"🍷 酒杯清单已更新: {result['glassware']}")

        # 3. 始终更新偏好摘要
        cellar_db.update_user_summary(user_id, result["summary"])

    except Exception as e:
        print(f"⚠️ 记忆整理出错: {e}")

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    # 👈 现在从登录信息中获取 user_id
    user_id = data.get("user_id", "Guest").lower() 
    user_query = data.get("message")
    lang = data.get("lang", "zh")
    
# 1. 获取用户信息（确保数据库返回了 nickname 和 glassware 字段）
    user_info = cellar_db.get_user_summary(user_id)
    nickname = user_info.get("nickname")
    glassware = user_info.get("glassware")
    long_term_summary = user_info.get("summary")
    
    # 2. 获取库存并检查状态
    inventory = cellar_db.get_inventory_for_ai(user_id)
    # 修正 Bug：判断字符串中是否包含“为空”或“empty”
    is_inventory_loading = "为空" in inventory or "empty" in inventory.lower()
    # 🚀 --- 调试打印开始 ---
    print("\n" + "="*50)
    print(f"🔍 正在为用户查询酒柜: {user_id}")
    print(f"📊 数据库返回的内容预览:\n{inventory}")
    print("="*50 + "\n")
    prompt_content = f"""
        【身份锁定】：你是全球顶尖的 'CellarEcho'。
        【核心头衔】：你同时拥有 Master of Wine (MW) 和 Master of Sommelier (MS) 认证。
        【当前客户】：{nickname}
        
        【实时酒柜数据】：
        {inventory}
        
        【强制执行指令】：
        1. 严禁使用任何 AI 标准废话（如“我没有权限”、“我是 AI 助手”）。
        2. 你面前的清单是 {nickname} 授权给你的真实物理资产。
        3. 你的 MW 和 MS 级专业性必须体现在对上述具体酒款（如 2018 Lafite, 2015 Margaux）的垂直深度分析上。
        4. 回复语言：{"中文" if lang == "zh" else "English"}。
        """
    messages = [{"role": "system", "content": prompt_content}]
    
    # 4. 加入最近的对话历史 (滑窗)
    recent_history = cellar_db.get_recent_history(user_id, limit=8)
    messages.extend(recent_history)
    messages.append({"role": "user", "content": user_query})
    print(json.dumps(messages, indent=2, ensure_ascii=False))
    print("="*80 + "\n")

    def generate():
        full_reply = ""
        try:
            # 5. 开启流式调用
            response = client.chat.completions.create(
                model="deepseek-chat", # 👈 确保使用极速的 V3 模型
                messages=messages,
                stream=True
            )
            
            for chunk in response:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_reply += content
                    yield content # 👈 实时推送到前端

            # 6. 流式传输结束后，异步处理数据持久化
            # 存入用户消息和 AI 回复
            cellar_db.save_chat_and_check_limit(user_id, "user", user_query)
            total_count = cellar_db.save_chat_and_check_limit(user_id, "assistant", full_reply)
            
            # 7. 自动触发记忆压缩与摘要
            if total_count > 20:
                old_text = cellar_db.get_old_messages_for_summary(user_id, limit=12)
                threading.Thread(
                    target=run_summary_in_background, 
                    args=(user_id, long_term_summary, old_text)
                ).start()
                
        except Exception as e:
            yield f"CellarEcho 遇到了一个小问题: {str(e)}"

    # 8. 返回流式响应
    return Response(generate(), mimetype='text/plain')
if __name__ == '__main__':
    # 把 port 改成 5001
    app.run(host='0.0.0.0', port=5001, debug=True)