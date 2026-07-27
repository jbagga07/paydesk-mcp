import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "paydesk")

# Startup Validation
if not MONGO_URI:
    raise ValueError("Startup Error: Environment variable 'MONGO_URI' is required but missing.")

client = MongoClient(MONGO_URI)
db = client[MONGO_DATABASE]


def get_db():
    return db