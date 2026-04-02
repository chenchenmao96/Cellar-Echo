import os
from pymongo import MongoClient

class CellarDB:
    def __init__(self):
        # 从环境变量获取 URI，如果没有则报错提醒
        mongo_uri = os.environ.get("MONGO_URI")
        if not mongo_uri:
            print("⚠️ 警告：未检测到 MONGO_URI 环境变量")
        
        self.client = MongoClient(mongo_uri)
        self.db = self.client.get_database("CellarEcho")
        self.collection = self.db.wines

    def update_inventory(self, wine_list):
        """覆盖式更新酒窖清单"""
        if not wine_list:
            return 0
        
        # 1. 清空当前所有数据
        self.collection.delete_many({})
        # 2. 插入新数据
        result = self.collection.insert_many(wine_list)
        return len(result.inserted_ids)

    def get_all_wines(self, limit=50):
        """获取酒窖数据用于 AI 上下文"""
        wines = list(self.collection.find({}, {"_id": 0}).limit(limit))
        # 转换成易于 AI 阅读的格式
        return "\n".join([str(w) for w in wines])

# 实例化，方便 app.py 调用
cellar_db = CellarDB()