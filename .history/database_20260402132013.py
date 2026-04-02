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
        self.history_collection = self.db.chat_history
        # 👈 新增：存放用户长期画像的集合
        self.profile_collection = self.db.user_profiles

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
    # --- 1. 获取长期摘要 ---
    def get_user_summary(self, user_id):
        """读取陈陈的长期偏好摘要"""
        profile = self.profile_collection.find_one({"user_id": user_id})
        return profile.get("summary", "暂无长期偏好记录。") if profile else "这是新用户的第一次对话。"

    # database.py 改进逻辑
    def get_user_summary(self, user_id):
        profile = self.profile_collection.find_one({"user_id": user_id})
        if profile:
            return profile.get("summary")
        
        # 💡 针对新用户的“第一印象”引导
        return "这是一位新用户。你可以根据酒窖清单，主动询问他的口味偏好，并提供专业的侍酒建议。"
    # --- 3. 存入新对话并检查长度 ---
    def save_chat_and_check_limit(self, user_id, role, content):
        """保存对话，并返回当前历史总数"""
        self.history_collection.insert_one({
            "user_id": user_id,
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        return self.history_collection.count_documents({"user_id": user_id})

    # --- 4. 提取旧消息用于摘要 ---
    def get_old_messages_for_summary(self, user_id, limit=12):
        """取出最老的 N 条消息用于压缩，取完后删除它们"""
        old_chats = list(self.history_collection.find({"user_id": user_id})
                         .sort("timestamp", 1).limit(limit))
        
        # 格式化成文本供 AI 阅读
        text_to_summarize = "\n".join([f"{c['role']}: {c['content']}" for c in old_chats])
        
        # 删除这些已处理的旧消息
        ids_to_delete = [c["_id"] for c in old_chats]
        self.history_collection.delete_many({"_id": {"$in": ids_to_delete}})
        
        return text_to_summarize

    # --- 5. 获取最近对话（滑窗） ---
    def get_recent_history(self, user_id, limit=8):
        cursor = self.history_collection.find({"user_id": user_id}).sort("timestamp", -1).limit(limit)
        history = list(cursor)[::-1]
        # 如果 history 是空的，这里会返回 []，DeepSeek 的 messages.extend([]) 会正常工作，不会报错
        return [{"role": h["role"], "content": h["content"]} for h in history]
cellar_db = CellarDB()