from pymongo import MongoClient
from dotenv import load_dotenv
import os


load_dotenv()



DATABASE_NAME = os.getenv("MONGO_DATABASE_NAME")
DATABASE_URL = os.getenv("MONGO_DATABASE_URL")


_db_client = None


def get_db():
    global _db_client
    try:
        if _db_client is None:
            _db_client = MongoClient(DATABASE_URL)
            print("CREATED MONGODB CONNECTION")
        return _db_client[DATABASE_NAME]
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        raise


