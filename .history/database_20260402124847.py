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
    # database.py 增加两个方法
class CellarDB:
    # ... 原有代码 ...
    def save_chat(self, user_id, role, content):
        self.db.chat_history.insert_one({
            "user_id": user_id,
            "role": role,
            "content": content,
            "timestamp": time.time()
        })

    def get_chat_history(self, user_id, limit=6):
        # 只取最近的 6 条对话（3 轮问答），节省 Token
        cursor = self.db.chat_history.find({"user_id": user_id}).sort("timestamp", -1).limit(limit)
        history = list(cursor)[::-1] # 翻转回正序
        return [{"role": h["role"], "content": h["content"]} for h in history]
cellar_db = CellarDB()