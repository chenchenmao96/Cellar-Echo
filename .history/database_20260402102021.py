import os
from pymongo import MongoClient

class CellarDB:
    def __init__(self):
        self.uri = os.environ.get("MONGO_URI")
        self.client = MongoClient(self.uri)
        self.db = self.client.get_database("CellarEcho")
        self.collection = self.db.wines

    def sync_inventory(self, user_id, wine_data):
        """
        为特定用户同步酒窖。
        注意：这里只删除该用户的数据，不影响其他用户。
        """
        if not wine_data:
            return 0
        
        try:
            # 1. 注入用户标签：为每瓶酒加上所有者 ID
            for wine in wine_data:
                wine['user_id'] = user_id
            
            # 2. 只删除当前用户的旧数据（数据隔离的关键）
            self.collection.delete_many({"user_id": user_id})
            
            # 3. 存入新数据
            result = self.collection.insert_many(wine_data)
            return len(result.inserted_ids)
        except Exception as e:
            print(f"MongoDB 同步失败: {e}")
            return 0

    def get_inventory_for_ai(self, user_id, limit=500):
        """
        只读取属于该用户的藏酒
        """
        try:
            # 核心：增加查询过滤条件 {"user_id": user_id}
            cursor = self.collection.find(
                {"user_id": user_id, "Quantity": {"$gt": 0}}, 
                {"_id": 0, "user_id": 0} # 传给 AI 时隐藏 user_id 节省 Token
            ).limit(limit)
            
            wines = list(cursor)
            if not wines:
                return "当前酒窖为空，请先同步数据。"
            
            header = "Wine | Vintage | Varietal | DrinkBetween\n"
            rows = [f"{w.get('Wine')} | {w.get('Vintage')} | {w.get('Varietal')} | {w.get('DrinkBetween')}" for w in wines]
            return header + "\n".join(rows)
        except Exception as e:
            return f"获取失败: {str(e)}"

cellar_db = CellarDB()