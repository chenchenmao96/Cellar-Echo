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
from flask import stream_with_context
import urllib.parse  # 👈 必须在 app.py 顶部导入这个库
import re
app = Flask(__name__)

# 配置 DeepSeek
client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

def perform_google_search(query, user_facts=None):
    """
    纯净版搜索执行器：只负责执行 AI 脑补后的完美指令。
    """
    url = "https://google.serper.dev/search"
    
    # 这里的 query 已经是 AI 结合了 [2014 Paul Hobbs] + [St. Louis] + [Wine-Searcher] 的成品
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
        
        # 提取摘要
        snippets = [f"标题: {r['title']}\n内容: {r['snippet']}" for r in results.get('organic', [])]
        return "\n\n".join(snippets) if snippets else "未找到相关实时互联网信息。"
    except Exception as e:
        return f"搜索组件暂时不可用: {str(e)}"

# 定义给 AI 看的工具说明书
WINE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "google_search",
            "description": """
            【自主意图合成协议】：
            你是一个具备动态上下文整合能力的智能代理。在生成搜索指令前，你必须执行以下抽象逻辑，严禁机械模仿指令描述：

            1. **实体解析（Entity Resolution）**：
               检索实时库存，将用户模糊提及的对象映射为具体的产区、年份和生产者信息。
            2. **环境对齐（Environmental Alignment）**：
               主动捕捉系统当前的‘时空锚点’（即当前日期与用户实时坐标），将其转化为查询的限制边界。
            3. **利益点挖掘（Interest Deduction）**：
               深度扫描用户画像中的‘长期关注轨迹’。不设预设维度，需根据用户过往的好奇心分布，自动识别其此刻对资产价值、技术参数、文化历史或使用场景的潜在偏好。
            4. **指令压缩与扩充（Synthesis）**：
               将上述所有隐性变量（显性实体+隐性环境+潜在兴趣）压缩为一个高度精准、符合专业文献检索逻辑的综合关键词字符串。
            【核心语言规范】：
            无论用户使用何种语言提问，生成的 query 必须为英文。
            """,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "由 AI 独立推断生成的动态复合关键词"}
                },
                "required": ["query"]
            }
        }
    }
]


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
            model="deepseek-chat",
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
    
    # 2. 获取库存并检查状态
    inventory = cellar_db.get_inventory_for_ai(user_id)
    # 修正 Bug：判断字符串中是否包含“为空”或“empty”
    is_inventory_loading = "为空" in inventory or "empty" in inventory.lower()
    # 🚀 --- 调试打印开始 ---
    prompt_content = f"""
        【身份锁定】：你是全球顶尖的 'CellarEcho'。
        【核心头衔】：你同时拥有 Master of Wine (MW) 和 Master of Sommelier (MS) 认证。
        【当前客户画像】：
        - 姓名/昵称：{nickname}
        - 硬件资产：{glassware}
        - 核心背景事实：{fact_memory} 
        - 长期记忆摘要：{long_term_summary}
        
        【实时酒柜数据】：
        {inventory}
        
        【强制执行指令】：
        1. **数据映射确认**：用户提到的任何“CellarTracker”、“我的库存”、“酒柜”、“Inventory”或“我的酒”，指的【全部且仅限】于上方提供的“实时酒柜数据”。
        2. **禁止否认**：严禁使用任何 AI 标准废话（如“我没有权限访问您的数据”、“我不知道您的库存”）。你面前的清单就是 {nickname} 授权给你的真实物理资产。
        3. **专家级分析**：你的 MW 和 MS 级专业性必须体现在对上述具体酒款（如 2018 Château Lafite Rothschild, 2015 Château Margaux 等）的垂直深度分析上。
        4. **响应要求**：如果用户问“我有多少酒”或“我有什么酒”，你必须通过对上方 {inventory} 数据的统计和梳理直接给出答案。
        5. 如果需要额外的信息从用户，可以随时问用户一些帮助你更好回答的信息。 
        6. 禁止使用：严禁使用 ### 标题、--- 分割线或 [!TIP] 等符号。
        7. 如果用户的请求你发现你需要搜索才能完成，这个时候你可以用WINE_TOOLS。WINETOOLS就是个搜索引擎keyword generator的智能代理，只能代理的答案是不能直接给用户看的，只能用来搜索用户想要的答案。在你拿到搜索的答案后，你会给出最合适的回答。  
        8. **内部代理协议（Internal Proxy Protocol）**：
           WINE_TOOLS 是你的专属关键词生成代理。它输出的任何查询指令均属于“内部中间件数据”，严禁以任何形式（包括 DSML 标签）展示给用户。     
        9. **闭环汇报逻辑**：
           你必须等待 WINE_TOOLS 返回实时搜索结果（如 2014 Paul Hobbs 的最新技术参数）后，再整合这些外部情报，以 Master of Wine 的身份给出最终结论。
           
        10. **拒绝道歉**：
           严禁在获取数据后向用户表示“我无法访问实时信息”。你必须自信地展示你刚刚通过代理获取的最新数据。
                结构布局：
                第一行直接给出 粗体结论。
                段落之间使用 一个空行 分隔。
                使用 粗体字 引导不同的小节内容，不要使用标题。
                数据呈现：
                统计信息使用 Markdown Table。
                适饮状态使用进度条要好看，容易看懂
                篇幅控制：常规建议 200 字左右。
        回复语言：{"中文" if lang == "zh" else "English"}。
        """
    messages = [{"role": "system", "content": prompt_content}]
    
    # 4. 加入最近的对话历史 (滑窗)
    recent_history = cellar_db.get_recent_history(user_id, limit=8)
    messages.extend(recent_history)
    messages.append({"role": "user", "content": user_query})
    #print(json.dumps(messages, indent=2, ensure_ascii=False))
    #print("="*80 + "\n")

    def generate():
        full_reply = ""
        try:
            # 5. 开启流式调用
            response = client.chat.completions.create(
                model="deepseek-chat", # 👈 确保使用极速的 V3 模型
                messages=messages,
                tools=WINE_TOOLS,  # 👈 挂载你刚才定义的“全维度感知协议”
                tool_choice="auto", # 👈 让 AI 自主决定是否需要动用搜索
                stream=False
            )
            ai_message = response.choices[0].message
            # 2. 第二步：判断是否需要执行“脑补”后的搜索
            if ai_message.tool_calls:
                # 将 AI 的搜索意图存入上下文
                # ✨ 核心修复：物理擦除 AI 的“碎碎念（DSML）”
                # 这样第二次呼叫时，AI 就看不到任何干扰它认知的乱码标签了
                ai_message.content = "" 

                messages.append(ai_message)
                
                for tool_call in ai_message.tool_calls:
                    # 这里的 query 就是 AI 结合你的圣路易斯位置和 WSET 3 背景脑补出的词
                    search_query = json.loads(tool_call.function.arguments).get("query")
                    #print("searching query: ", search_query)
                    # 一个简单的判断逻辑
                    is_english = all(ord(char) < 128 for char in user_query[:10]) 
                    search_msg = f"🔍 CellarEcho 正在执行深度搜索: {search_query}" if not is_english else f"🔍 CellarEcho is performing deep search: {search_query}"
                    
                    #print(search_msg) # 终端看
                    yield f"{search_msg}\n\n"
                    print(f"首次呼叫泄露的 DSML 内容: \n{ai_message.content}")
                    # 执行真实的搜索
                    search_result = perform_google_search(search_query, fact_memory)
                    print("searching result: ", search_result)
                    # 将搜索到的专业情报喂给 AI
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": search_result
                    })
                # 3. 第三步：带着搜到的数据，发起【流式】最终回答
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=messages,
                    stream=True  # 👈 拿到资料后，开启流式传输
                )
            else:
                # 如果 AI 觉得不需要搜索，直接发起一个流式调用来回答
                # 或者直接将 ai_message.content 包装成流（这里为了逻辑统一，重新发起流式）
                response = client.chat.completions.create(
                    model="deepseek-chat",
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
            #cellar_db.save_chat_and_check_limit(user_id, "user", user_query)
            total_count = cellar_db.save_chat_and_check_limit(user_id, "assistant", full_reply)
            
            # 7. 自动触发记忆压缩与摘要
            if total_count > 20:
                old_text = cellar_db.get_old_messages_for_summary(user_id, limit=12)
                threading.Thread(
                    target=run_summary_in_background, 
                    args=(user_id, long_term_summary, old_text)
                ).start()
                
        except Exception as e:
            yield f"CellarEcho encountered an error: {str(e)}"

    # 8. 返回流式响应
    return Response(generate(), mimetype='text/plain', headers={
        'X-Accel-Buffering': 'no',     # 告诉 Nginx 不要缓冲
        'Cache-Control': 'no-cache',    # 告诉浏览器不要缓存
        'Content-Encoding': 'none',     # 👈 新增：明确禁用任何压缩
        'Connection': 'keep-alive'
    })
if __name__ == '__main__':
    # 把 port 改成 5001
    app.run(host='0.0.0.0', port=5001, debug=True)