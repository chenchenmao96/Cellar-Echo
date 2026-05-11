import os, requests, pandas as pd
import json, time, threading
from flask import Flask, request, jsonify, render_template, Response
from database import cellar_db
from io import StringIO
import urllib.parse
from openai import OpenAI

app = Flask(__name__)

# ================= 初始化 DeepSeek 客户端 =================
client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

# ================= 搜索工具定义 =================
def perform_google_search(query):
    """
    执行真实的 Google 搜索（通过 Serper API）
    """
    url = "https://google.serper.dev/search"
    payload = json.dumps({
        "q": query,
        "gl": "us",
        "hl": "en",
        "num": 4
    })
    headers = {
        'X-API-KEY': os.environ.get("SERPER_API_KEY"),
        'Content-Type': 'application/json'
    }
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=8)
        results = response.json()
        snippets = [f"标题: {r['title']}\n内容: {r['snippet']}" for r in results.get('organic', [])]
        return "\n\n".join(snippets) if snippets else "未找到相关实时信息。"
    except Exception as e:
        return f"搜索组件暂时不可用: {str(e)}"

# DeepSeek 使用的工具描述（Function Calling 格式）
WINE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "google_search",
            "description": """
当需要获取实时信息、最新数据或 Wine-Searcher 等平台的专业评价时使用。
必须由 AI 根据上下文（用户当前位置、偏好、库存酒款等）自动推断出最佳英文搜索关键词，
严禁直接使用用户原始问题。
            """,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "由 AI 自主生成的英文复合搜索词"}
                },
                "required": ["query"]
            }
        }
    }
]

# ================= 基础路由 =================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    password = data.get("password")
    username = data.get("username").lower()
    test_url = f"https://www.cellartracker.com/xlquery.asp?User={username}&Password={password}&Format=csv&Table=Inventory"
    try:
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
    ct_pass = data.get("ct_pass")
    if not ct_pass:
        return jsonify({"status": "error", "message": "未接收到有效密码"}), 400
    safe_pass = urllib.parse.quote(ct_pass)
    url = f"https://www.cellartracker.com/xlquery.asp?User={user_id}&Password={safe_pass}&Format=csv&Table=Inventory"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200 and "Wine" in response.text:
            df = pd.read_csv(StringIO(response.text))
            if 'QuantityCommunity' in df.columns:
                wine_data = df[df['QuantityCommunity'] > 0].to_dict('records')
                count = cellar_db.sync_inventory(user_id, wine_data)
                return jsonify({"status": "success", "count": count})
            else:
                return jsonify({"status": "error", "message": "CSV 格式不匹配"}), 400
        return jsonify({"status": "error", "message": "CellarTracker 拒绝访问或账号错误"}), 401
    except Exception as e:
        print(f"❌ 同步异常: {str(e)}")
        return jsonify({"status": "error", "message": "服务器内部错误"}), 500

# ================= 记忆管理 =================
def run_summary_in_background(user_id, long_term_summary, old_text):
    try:
        user_info = cellar_db.get_user_summary(user_id)
        curr_nickname = user_info.get("nickname")
        curr_glassware = user_info.get("glassware")
        curr_facts = user_info.get("fact_memory", {})

        task_prompt = f"""
你是 CellarEcho 的记忆管理员。请审计最近对话并更新档案。

当前档案：
- 昵称：{curr_nickname}
- 酒杯：{curr_glassware}
- 已知事实：{curr_facts}
- 旧摘要：{long_term_summary}

最近对话：
{old_text}

仅根据对话内容，用 JSON 返回（保持无变化则字段不变）：
{{
    "nickname": "仅改名时更新",
    "glassware": "仅更换时更新",
    "facts": {{"新事实key": "value"}},
    "summary": "融合后的新摘要"
}}
若无新事实，facts 返回空对象 {{}}。
        """
        res = client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": task_prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(res.choices[0].message.content)

        if result.get("nickname") and result["nickname"] != curr_nickname:
            cellar_db.update_nickname(user_id, result["nickname"])
        if result.get("glassware") and result["glassware"] != curr_glassware:
            cellar_db.update_glassware(user_id, result["glassware"])

        cellar_db.update_memories(
            user_id=user_id,
            facts=result.get("facts"),
            chat_summary=result.get("summary")
        )
        print(f"✨ {user_id} 记忆已更新")
    except Exception as e:
        print(f"⚠️ 记忆整理出错: {e}")

@app.route('/get_history', methods=['GET'])
def get_history():
    user_id = request.args.get("user_id", "Guest").lower()
    try:
        history = cellar_db.get_recent_history(user_id, limit=50)
        return jsonify({"status": "success", "history": history})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ================= 核心对话端点（加固版） =================
@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_id = data.get("user_id", "Guest").lower()
    user_query = data.get("message")
    lang = data.get("lang", "zh")

    cellar_db.save_chat_and_check_limit(user_id, "user", user_query)

    # 用户画像 & 库存
    user_info = cellar_db.get_user_summary(user_id)
    nickname = user_info.get("nickname")
    glassware = user_info.get("glassware")
    fact_memory = user_info.get("fact_memory")
    long_term_summary = user_info.get("summary")
    inventory = cellar_db.get_inventory_for_ai(user_id)

    system_prompt = f"""
【身份锁定】：你是全球顶尖的 'CellarEcho'。
【头衔】：Master of Wine (MW) & Master Sommelier (MS)。
【当前客户画像】：
- 姓名/昵称：{nickname}
- 醒酒器/杯具：{glassware}
- 背景事实：{fact_memory}
- 长期记忆摘要：{long_term_summary}

【实时酒柜数据】：
{inventory}

【强制执行指令】：
1. 用户提及“我的酒 / inventory / 酒柜”只能对应上方实时数据。
2. 严禁否认数据、使用“我没有权限”等字眼。
3. 所有分析必须体现 MW/MS 级专业性。
4. 可用工具：google_search。当需要实时信息时自主生成英文关键词调用，禁止直接暴露查询词给用户。
5. 收到搜索结果后立即停止调用工具，直接给出最终专业建议。
6. 禁用 ### / --- / [!TIP] 等符号。
7. 输出结构：粗体结论 → 分段说明 → Markdown 表格 → 适饮进度条。
回复语言：{"中文" if lang == "zh" else "English"}。
    """

    messages = [{"role": "system", "content": system_prompt}]

    # 加载最近历史（需要确保返回 [{"role":"user","content":"..."}, ...] 格式）
    recent_history = cellar_db.get_recent_history(user_id, limit=8)
    # 安全转换（兼顾数据库可能返回不同格式）
    for msg in recent_history:
        if 'role' in msg and 'content' in msg:
            # 确保 role 是 "user" 或 "assistant"
            role = "user" if msg["role"] in ("user", "assistant") else "user"
            messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_query})

    def generate():
        full_reply = ""
        try:
            # 第一次调用：AI 可能触发搜索
            first_response = client.chat.completions.create(
                model="deepseek-v4-pro",
                messages=messages,
                tools=WINE_TOOLS,
                tool_choice="auto",
                stream=False
            )
            ai_message = first_response.choices[0].message

            # 需要搜索？
            if ai_message.tool_calls:
                # 提取搜索词并告诉前端
                search_query = json.loads(ai_message.tool_calls[0].function.arguments).get("query", "")
                yield f"🔍 正在深度搜索：{search_query}\n\n"

                # 将 AI 的工具调用请求存入上下文（但清空可能泄露 DSML 的 content）
                ai_message.content = ""
                messages.append(ai_message)

                # 执行所有工具调用
                for tool in ai_message.tool_calls:
                    q = json.loads(tool.function.arguments).get("query", "")
                    print(f"[SEARCH] {user_id} -> {q}")
                    result = perform_google_search(q)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": result
                    })

                # 强制禁止再次调用工具
                messages.append({
                    "role": "system",
                    "content": "Data received. Do NOT use any more tools. Provide the final answer directly."
                })

                # 第二次调用：流式输出最终答案
                final_stream = client.chat.completions.create(
                    model="deepseek-v4-pro",
                    messages=messages,
                    tool_choice="none",
                    stream=True
                )
                for chunk in final_stream:
                    if chunk.choices[0].delta.content:
                        text = chunk.choices[0].delta.content
                        full_reply += text
                        yield text
                # 标记搜索已使用
                if ai_message.tool_calls:
                    yield "__GOOGLE_SEARCH_USED__"
            else:
                # 无需搜索，直接流式输出
                direct_stream = client.chat.completions.create(
                    model="deepseek-v4-pro",
                    messages=messages,
                    tool_choice="none",
                    stream=True
                )
                for chunk in direct_stream:
                    if chunk.choices[0].delta.content:
                        text = chunk.choices[0].delta.content
                        full_reply += text
                        yield text

            # 保存 AI 回复，触发记忆压缩
            total = cellar_db.save_chat_and_check_limit(user_id, "assistant", full_reply)
            if total > 20:
                old_text = cellar_db.get_old_messages_for_summary(user_id, limit=12)
                threading.Thread(
                    target=run_summary_in_background,
                    args=(user_id, long_term_summary, old_text)
                ).start()

        except Exception as e:
            yield f"CellarEcho 遇到错误：{str(e)}"

    return Response(generate(), mimetype='text/plain', headers={
        'X-Accel-Buffering': 'no',
        'Cache-Control': 'no-cache',
        'Content-Encoding': 'none',
        'Connection': 'keep-alive'
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)