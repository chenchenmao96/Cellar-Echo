# database.py 修正版
import os
import time
from pymongo import MongoClient

class CellarDB:
    def __init__(self):
        self.uri = os.environ.get("MONGO_URI")
        self.client = MongoClient(self.uri)
        # 统一数据库名为 CellarEcho
        self.db = self.client.get_database("CellarEcho") 
        self.collection = self.db.wines
        self.history_collection = self.db.chat_history
        self.profile_collection = self.db.user_profiles

    def sync_inventory(self, user_id, wine_data):
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

    def get_inventory_for_ai(self, user_id):
            user_id = user_id.lower() 
            try:
                # 1. 核心修改：只排除 _id，保留所有其他字段
                cursor = self.collection.find(
                    {"user_id": user_id, "QuantityCommunity": {"$gt": 0}},
                    {"_id": 0} 
                ).sort("CT", -1)
                
                wines = list(cursor)
                if not wines: 
                    return "当前酒窖为空，请先同步数据。"
                
                # 2. 动态构建完整信息
                inventory_list = []
                for i, w in enumerate(wines):
                    # 将该条记录的所有 Key-Value 拼接成字符串
                    # 这样 AI 就能看到包括 Varietal, Vineyard, MasterPlaylist 等所有信息
                    details = ", ".join([f"{k}: {v}" for k, v in w.items() if v and str(v) != 'nan'])
                    inventory_list.append(f"[{i+1}] {details}")
                
                return "\n".join(inventory_list)
                
            except Exception as e:
                return f"获取全量数据失败: {str(e)}"

    # database.py 
    # database.py 

    def update_memories(self, user_id, facts=None, chat_summary=None):
            """统一写入逻辑"""
            update_data = {}
            if facts:
                # 使用点号表示法更新嵌套字典，不会覆盖掉旧的其他 facts
                for k, v in facts.items():
                    update_data[f"fact_memory.{k}"] = v
            if chat_summary:
                update_data["summary"] = chat_summary  # 👈 统一改为 summary

            self.profile_collection.update_one(
                {"user_id": user_id},
                {"$set": update_data},
                upsert=True
            )

    def get_user_summary(self, user_id):
        """全量读取画像：包含昵称、杯型、事实和摘要"""
        profile = self.profile_collection.find_one({"user_id": user_id})
        if profile:
            return {
                "nickname": profile.get("nickname"),
                "glassware": profile.get("glassware", "未记录"),
                "summary": profile.get("summary", "新用户。"),
                "fact_memory": profile.get("fact_memory", {})  # 👈 读出你的硬事实
            }
        return {"summary": "新用户。", "nickname": None, "glassware": "未记录", "fact_memory": {}}

    def update_nickname(self, user_id, nickname):
        """保存用户希望被称呼的名字"""
        self.profile_collection.update_one(
            {"user_id": user_id},
            {"$set": {"nickname": nickname}},
            upsert=True
        )
    def update_glassware(self, user_id, glassware_list):
        """保存用户的酒杯收藏（传入列表或字符串）"""
        self.profile_collection.update_one(
            {"user_id": user_id},
            {"$set": {"glassware": glassware_list}},
            upsert=True
        )
    def save_chat_and_check_limit(self, user_id, role, content):
        """保存对话，并返回当前历史总数"""
        self.history_collection.insert_one({
            "user_id": user_id,
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        return self.history_collection.count_documents({"user_id": user_id})

    def get_old_messages_for_summary(self, user_id, limit=12):
        old_chats = list(self.history_collection.find({"user_id": user_id})
                         .sort("timestamp", 1).limit(limit))
        text_to_summarize = "\n".join([f"{c['role']}: {c['content']}" for c in old_chats])
        ids_to_delete = [c["_id"] for c in old_chats]
        self.history_collection.delete_many({"_id": {"$in": ids_to_delete}})
        return text_to_summarize

    def get_recent_history(self, user_id, limit=8):
        cursor = self.history_collection.find({"user_id": user_id}).sort("timestamp", -1).limit(limit)
        history = list(cursor)[::-1]
        return [{"role": h["role"], "content": h["content"]} for h in history]

cellar_db = CellarDB()