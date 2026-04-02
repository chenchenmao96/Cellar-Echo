# database.py 修正版
import os
from pymongo import MongoClient

class CellarDB:
    def __init__(self):
        self.uri = os.environ.get("MONGO_URI")
        self.client = MongoClient(self.uri)
        # 建议数据库名保持一致
        self.db = self.client.get_database("CellarEcho") 
        self.collection = self.db.wines

    def sync_inventory(self, user_id, wine_data):
        # ... 这里保留你原来的代码 ...
        if not wine_data: return 0
        try:
            for wine in wine_data:
                wine['user_id'] = user_id
            self.collection.delete_many({"user_id": user_id})
            result = self.collection.insert_many(wine_data)
            return len(result.inserted_ids)
        except Exception as e:
            print(f"MongoDB 同步失败: {e}")
            return 0

    # 【关键修改】确保这个函数在类里面（有缩进）
    def get_inventory_for_ai(self, user_id, limit=1000):
        try:
            cursor = self.collection.find(
                {"user_id": user_id, "QuantityCommunity": {"$gt": 0}}, # 注意列名
                {"_id": 0, "user_id": 0}
            ).sort("CT", -1).limit(limit)
            
            wines = list(cursor)
            if not wines: return "当前酒窖为空，请先同步数据。"

            full_report = []
            for i, w in enumerate(wines, 1):
                wine_info = ", ".join([f"{k}: {v}" for k, v in w.items() if v])
                full_report.append(f"[{i}] {wine_info}")
            return "\n".join(full_report)
        except Exception as e:
            return f"获取失败: {str(e)}"
# --- 🟢 新增：保存对话 ---
    def save_chat(self, user_id, role, content):
        """将单条对话存入数据库"""
        try:
            self.history_collection.insert_one({
                "user_id": user_id,
                "role": role,        # "user" 或 "assistant"
                "content": content,
                "timestamp": time.time()
            })
        except Exception as e:
            print(f"保存聊天记录失败: {e}")

    # --- 🟢 新增：获取最近的历史记录 (滑窗机制) ---
    def get_chat_history(self, user_id, limit=6):
        """获取最近的 N 条对话，用于维持上下文"""
        try:
            # 按时间倒序排，取最近的 limit 条
            cursor = self.history_collection.find({"user_id": user_id}).sort("timestamp", -1).limit(limit)
            # 拿到后需要翻转一下，变成正向的时间顺序发给 AI
            history = list(cursor)[::-1]
            # 格式化为 DeepSeek 要求的格式
            return [{"role": h["role"], "content": h["content"]} for h in history]
        except Exception as e:
            print(f"获取聊天记录失败: {e}")
            return []
cellar_db = CellarDB()