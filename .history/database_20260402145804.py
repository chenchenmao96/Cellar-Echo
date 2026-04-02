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

    def get_inventory_for_ai(self, user_id, limit=30):
        user_id = user_id.lower() # 👈 确保查询 Key 永远是小写
        try:
            cursor = self.collection.find(
                {"user_id": user_id, "QuantityCommunity": {"$gt": 0}},
                {"_id": 0, "Wine": 1, "Vintage": 1, "Varietal": 1, "DrinkBetween": 1}
            ).sort("CT", -1).limit(limit)
            
            wines = list(cursor)
            if not wines: return "当前酒窖为空，请先同步数据。"
            
            return "\n".join([f"[{i+1}] {w['Wine']} ({w['Vintage']}), 适饮期: {w['DrinkBetween']}" for i, w in enumerate(wines)])
        except Exception as e:
            return f"获取失败: {str(e)}"

    # database.py 

    def get_user_summary(self, user_id):
        """读取长期画像，同时检查是否有昵称"""
        profile = self.profile_collection.find_one({"user_id": user_id})
        if profile:
            # 如果有昵称，就返回摘要；如果没有，标记为需要询问
            nickname = profile.get("nickname")
            summary = profile.get("summary", "新用户。")
            return {"summary": summary, "nickname": nickname}
        
        return {"summary": "新用户。", "nickname": None}

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