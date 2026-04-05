import os, requests, pandas as pd
import json, time, threading
from flask import Flask, request, jsonify, render_template, Response
from database import cellar_db 
from io import StringIO
# 🚀 2026 规范导入方式
from google import genai
from google.genai import types # 👈 重点：这就是你要的 types 的出处
import urllib.parse  # 👈 必须在 app.py 顶部导入这个库
app = Flask(__name__)

# 实例化客户端（取代旧的 genai.configure）
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))



def to_gemini_format(history):
    formatted_history = []
    for m in history:
        content = m.get("content", "").strip()
        # 🚀 致命修复：过滤掉因为之前崩溃而存入数据库的空回复！
        # 否则 Gemini 会直接拒收并引发 400 错误。
        if not content: 
            continue
            
        role = "user" if m["role"] == "user" else "model"
        formatted_history.append({
            "role": role,
            "parts": [{"text": content}] 
        })
    return formatted_history

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    # 👈 现在从登录信息中获取 user_id
    user_id = data.get("user_id", "Guest").lower() 
    user_query = data.get("message")
    lang = data.get("lang", "zh")
    cellar_db.save_chat_and_check_limit(user_id, "user", user_query)
# 1. 获取用户信息（确保数据库返回了 nickname 和 glassware 字段）
    user_info = cellar_db.get_user_summary(user_id)
    nickname = user_info.get("nickname")
    glassware = user_info.get("glassware")
    fact_memory = user_info.get("fact_memory")
    long_term_summary = user_info.get("summary")
    recent_history = cellar_db.get_recent_history(user_id, limit=8)

        # 2. 获取库存并检查状态
    inventory = cellar_db.get_inventory_for_ai(user_id)
    system_instruction = f"""
        【身份锁定】：你是全球顶尖的 'CellarEcho'。
        【核心头衔】：你同时拥有 Master of Wine (MW) 和 Master of Sommelier (MS) 认证。
        【当前客户画像】：
        - 姓名/昵称：{nickname}
        - 硬件资产：{glassware}
        - 核心背景事实：{fact_memory} 
        - 长期记忆摘要：{long_term_summary}
        - 最近的聊天记录 :     {recent_history}
        
        【实时酒柜数据】：
        {inventory} 
        
        【强制执行指令】：
        1. **数据映射确认**：用户提到的任何“CellarTracker”、“我的库存”、“酒柜”、“Inventory”或“我的酒”，指的【全部且仅限】于上方提供的“实时酒柜数据”。
        2. **禁止否认**：严禁使用任何 AI 标准废话（如“我没有权限访问您的数据”、“我不知道您的库存”）。你面前的清单就是 {nickname} 授权给你的真实物理资产。
        3. **专家级分析**：你的 MW 和 MS 级专业性必须体现在对上述具体酒款（如 2018 Château Lafite Rothschild, 2015 Château Margaux 等）的垂直深度分析上。
        4. **响应要求**：如果用户问“我有多少酒”或“我有什么酒”，你必须通过对上方 {inventory} 数据的统计和梳理直接给出答案。
        5. 任何涉及主观判断的回复，必须以‘上下文闭环’为先决条件。若当前请求存在多义性或缺失核心约束（如受众、动机），你必须发起澄清对话，禁止在未对齐需求的情况下消耗回复额度。严禁为了交互而提问。仅当缺失变量导致你完全无法做出专业判断（如 MW 级别的配餐逻辑）时，才允许发起唯一一次澄清。
        6. 禁止使用：严禁使用 ### 标题、--- 分割线或 [!TIP] 等符号。
        7. 如果用户的请求你发现你需要搜索才能完成,请搜索。 
        8. **拒绝道歉**：
           严禁在获取数据后向用户表示“我无法访问实时信息”。你必须自信地展示你刚刚通过代理获取的最新数据。
                结构布局：
                第一行直接给出 粗体结论。
                段落之间使用 一个空行 分隔。
                使用 粗体字 引导不同的小节内容，不要使用标题。
                数据呈现：
                统计信息使用 Markdown Table。
                你是使用视觉化工具的大师
                篇幅控制：常规建议 200 字左右。
        回复语言：{"中文" if lang == "zh" else "English"}。
        """ 
    grounding_tool = types.Tool(
        google_search=types.GoogleSearch()
    )
    config = types.GenerateContentConfig(
        tools=[grounding_tool], 
        system_instruction=system_instruction,
        
    )
    def generate_stream():
        full_response = ""
        search_used = False
        try:
        # 使用流式接口
            responses = client.models.generate_content_stream(
                model="gemini-3-flash-preview",
                contents=user_query,
                config=config,
            )
            for chunk in responses:
                if hasattr(chunk.candidates[0], "grounding_metadata"):
                    metadata = chunk.candidates[0].grounding_metadata
                    if metadata:
                        search_used = True
                if chunk.text:
                    text_fragment = chunk.text
                    full_response += text_fragment
                    yield text_fragment
            if search_used:   
                yield "__GOOGLE_SEARCH_USED__"
                # 同时为了数据库存储的完整性，存入一个标准的 Markdown 注释
                # 这里的 footer 格式可以根据你对数据库的要求自定义
                #footer = "\n\n> *Note: This response was verified by Google Search.*"
                #full_response += footer
            total_count = cellar_db.save_chat_and_check_limit(user_id, "assistant", full_response)
                
                # 7. 自动触发记忆压缩与摘要
            if total_count > 20:
                old_text = cellar_db.get_old_messages_for_summary(user_id, limit=12)
                threading.Thread(
                    target=run_summary_in_background, 
                    args=(user_id, long_term_summary, old_text)
                ).start()
        except Exception as e:
            yield f"CellarEcho encountered an error: {str(e)}"

        
    return Response(generate_stream(), mimetype='text/plain', headers={
        'X-Accel-Buffering': 'no',     # 告诉 Nginx 不要缓冲
        'Cache-Control': 'no-cache',    # 告诉浏览器不要缓存
        'Content-Encoding': 'none',     # 👈 新增：明确禁用任何压缩
        'Connection': 'keep-alive'
    })
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
        # 🚀 链式调用：直接创建并运行
        res = client.models.generate_content(
            model='gemini-3-flash-preview', # 绝对锁定最新模型
            contents=task_prompt,    # 你的审计指令
            config=types.GenerateContentConfig(
                response_mime_type="application/json" # 强制 JSON 输出，确保后台解析不崩
            )
        )
        
        import json
        result = json.loads(res.text) # 直接读取文本即可，因为你开启了 JSON 模式

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