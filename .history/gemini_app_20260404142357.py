import os, requests, pandas as pd
import json, time, threading
from flask import Flask, request, jsonify, render_template, Response
import google.generativeai as genai
from database import cellar_db 

app = Flask(__name__)

# 1. 核心模型配置：锁定最新的 Gemini 3 Flash
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

def google_search(query: str):
    """
    CellarEcho 专属搜索协议：由 Gemini 3 Flash 驱动。
    """
    url = "https://google.serper.dev/search"
    payload = json.dumps({"q": query, "gl": "us", "hl": "en", "num": 4})
    headers = {'X-API-KEY': os.environ.get("SERPER_API_KEY"), 'Content-Type': 'application/json'}
    try:
        res = requests.post(url, headers=headers, data=payload, timeout=8).json()
        return "\n\n".join([f"标题: {r['title']}\n内容: {r['snippet']}" for r in res.get('organic', [])])
    except Exception as e:
        return f"搜索暂时不可用: {str(e)}"

# 2. 转换历史格式
def to_gemini_format(history):
    return [{"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]} for m in history]

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_id = data.get("user_id", "Guest").lower()
    user_query = data.get("message")
    
    user_info = cellar_db.get_user_summary(user_id)
    inventory = cellar_db.get_inventory_for_ai(user_id)
    
    # 专家人格注入
    system_instruction = f"""
    你是顶尖酒柜管家 'CellarEcho' (MW/MS 认证)。
    客户：{user_info.get('nickname')}，酒柜现状：{inventory}。
    指令：基于库存给出专业建议。如需查询拉菲 (Lafite) 等名庄的实时行情，请调用 google_search。
    """

    # 3. 初始化 Gemini 3 Flash
    model = genai.GenerativeModel(
        model_name='gemini-3-flash', # 👈 锁定最新旗舰模型
        tools=[google_search],
        system_instruction=system_instruction
    )

    def generate():
        full_reply = ""
        try:
            history = to_gemini_format(cellar_db.get_recent_history(user_id, limit=8))
            chat_session = model.start_chat(history=history)
            
            # 开启极速流式响应
            response = chat_session.send_message(user_query, stream=True)
            
            for chunk in response:
                for part in chunk.candidates[0].content.parts:
                    # 🚀 处理自动函数调用（无感执行）
                    if part.function_call:
                        q = part.function_call.args['query']
                        yield f"🔍 CellarEcho (Gemini 3) 正在扫描全球市场: {q}\n\n"
                    
                    # 🚀 处理最终文字输出
                    if part.text:
                        full_reply += part.text
                        yield part.text

            cellar_db.save_chat_and_check_limit(user_id, "assistant", full_reply)
        except Exception as e:
            yield f"CellarEcho 系统异常: {str(e)}"

    return Response(generate(), mimetype='text/plain')


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
    ct_pass = data.get("ct_pass")  # 👈 确认这里与前端 index.html 的键名一致
    
    if not ct_pass:
        return jsonify({"status": "error", "message": "未接收到有效密码"}), 400

    # 1. 核心修复：对密码进行 URL 编码
    # 这样即便密码里有 & 或 #，也不会破坏 CellarTracker 的请求链接
    safe_pass = urllib.parse.quote(ct_pass)
    url = f"https://www.cellartracker.com/xlquery.asp?User={user_id}&Password={safe_pass}&Format=csv&Table=Inventory"
    
    try:
        # 2. 增加超时控制，防止请求卡死
        response = requests.get(url, timeout=30)
        
        # 3. 稳健性检查：只有返回 200 且内容包含 "Wine" 才是真正的 CSV
        if response.status_code == 200 and "Wine" in response.text:
            df = pd.read_csv(StringIO(response.text))
            
            # 4. 容错处理：确保 CSV 结构正确
            if 'QuantityCommunity' in df.columns:
                # 过滤库存 > 0 的酒款
                wine_data = df[df['QuantityCommunity'] > 0].to_dict('records')
                
                # 5. 调用数据库同步逻辑
                count = cellar_db.sync_inventory(user_id, wine_data)
                return jsonify({"status": "success", "count": count})
            else:
                return jsonify({"status": "error", "message": "CSV 格式不匹配"}), 400
                
        return jsonify({"status": "error", "message": "CellarTracker 拒绝访问或账号错误"}), 401
        
    except Exception as e:
        # 6. 将具体的错误信息打出，方便你在日志里查看
        print(f"❌ 同步过程中发生异常: {str(e)}")
        return jsonify({"status": "error", "message": "服务器内部错误，请检查后台日志"}), 500

def run_summary_in_background(user_id, long_term_summary, old_text):
    """
    在后台像私人管家一样整理记忆。
    任务：识别称呼、资产、硬事实、以及滚动摘要。
    """
    try:
        # 1. 读取当前所有画像，作为 AI 工作的基准
        user_info = cellar_db.get_user_summary(user_id)
        curr_nickname = user_info.get("nickname")
        curr_glassware = user_info.get("glassware")
        curr_facts = user_info.get("fact_memory", {})

        # 2. 升级版 Prompt：增加对 facts 的提取
        task_prompt = f"""
                你是 CellarEcho 的记忆管理员。你现在的任务是【审计】用户最近的对话，并决定是否需要更新档案。

                ### 核心原则
                1. **证据优先**：所有提取的 Facts 必须【直接源自】下方的“最近对话”内容。
                2. **严禁抄袭示例**：Prompt 中括号内的内容（如 Eric, HCI博士）仅为格式参考，若对话中未出现，严禁写入返回结果。
                3. **保持现状**：如果对话中没有提到新的事实，则 `facts` 字段应返回空对象 {{}}。

                ### 输入数据
                【当前档案现状】：
                - 昵称：{curr_nickname}
                - 酒杯：{curr_glassware}
                - 已知事实：{curr_facts}
                - 旧摘要：{long_term_summary}

                【待审计的最近对话】：
                --- START OF DIALOGUE ---
                {old_text}
                --- END OF DIALOGUE ---

                ### 输出任务
                请分析上述【待审计的最近对话】，并按以下 JSON 格式输出：
                {{
                    "nickname": "仅当用户明确要求改名时更新，否则保持原样",
                    "glassware": "仅当提到新买或更换杯子时更新，否则保持原样",
                    "facts": {{
                        "新发现的Key": "对应的Value" 
                    }}, // 注意：若无新事实发现，请保持为 {{}}
                    "summary": "融合后的最新叙事性摘要"
                }}
                """

        res = client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": task_prompt}],
            response_format={ "type": "json_object" }
        )
        
        import json
        result = json.loads(res.choices[0].message.content)

        # 3. 智能同步：根据变化精准更新
        
        # A. 更新顶层属性（昵称和酒杯）
        if result.get("nickname") and result["nickname"] != curr_nickname:
            cellar_db.update_nickname(user_id, result["nickname"])
            
        if result.get("glassware") and result["glassware"] != curr_glassware:
            cellar_db.update_glassware(user_id, result["glassware"])

        # B. 调用统一的 update_memories 处理 Facts 和 Summary
        # 这里 result.get("facts") 会被 update_memories 里的 $set 逻辑处理，不会覆盖旧的无关事实
        cellar_db.update_memories(
            user_id=user_id, 
            facts=result.get("facts"), 
            chat_summary=result.get("summary")
        )

        print(f"✨ {user_id} 的后台记忆整理已完成")

    except Exception as e:
        print(f"⚠️ 记忆整理出错: {e}")


if __name__ == '__main__':
    # 把 port 改成 5001
    app.run(host='0.0.0.0', port=5001, debug=True)