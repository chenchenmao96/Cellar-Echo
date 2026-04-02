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

def get_inventory_for_ai(self, user_id, limit=1000): # 既然用 DeepSeek，直接拉到 1000
    """
    全量读取该用户的藏酒信息，动态返回所有可用字段
    """
    try:
        # 1. 查询该用户所有库存大于 0 的酒
        # 排序建议：按 CT 分数从高到低，确保最有价值的酒排在前面
        cursor = self.collection.find(
            {"user_id": user_id, "Quantity": {"$gt": 0}}, 
            {"_id": 0, "user_id": 0} # 依然隐藏内部 ID 和标签
        ).sort("CT", -1).limit(limit)
        
        wines = list(cursor)
        if not wines:
            return "当前酒窖为空，请先同步数据。"

        # 2. 动态构建“全息清单”
        # 我们不再手动写 Wine | Vintage... 而是让 Python 自动读出所有 Key
        full_report = []
        for i, w in enumerate(wines, 1):
            # 将每一瓶酒的所有属性转换成类似 "Wine: Almaviva, Vintage: 2020..." 的格式
            wine_info = ", ".join([f"{k}: {v}" for k, v in w.items() if v])
            full_report.append(f"[{i}] {wine_info}")

        # 3. 合并成一个大文本返回给 DeepSeek
        return "\n".join(full_report)

    except Exception as e:
        return f"获取失败: {str(e)}"

cellar_db = CellarDB()