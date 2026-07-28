#pymongo is pythons class to connect to mongoDB 
from pymongo import MongoClient
#json library to read json
import json
#pathlib library to work with file paths  or folders (working with folders)
from pathlib import Path


# Connect to MongoDB
client = MongoClient("mongodb://localhost:27017/")

#Selecting the datbase, if not exists then creates automatically 
db = client["paydesk"]

# -------------------------------------------------
# Find the project root directory
# -------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent   #__file__ means where am I 

# Path to data/mongodb
DATA = BASE_DIR / "data" / "mongodb"

# -------------------------------------------------
# Clear old data (optional but recommended)
# -------------------------------------------------
db.transactions.delete_many({})
db.tickets.delete_many({})
db.disputes.delete_many({})


# -------------------------------------------------
# Load Transactions
# -------------------------------------------------
with open(DATA / "transactions.json", "r", encoding="utf-8") as f:
    transactions = json.load(f)

print(f"Loaded {len(transactions)} transactions")

db.transactions.insert_many(transactions)

print("✅ Transactions inserted")

# -------------------------------------------------
# Load Tickets
# -------------------------------------------------
with open(DATA / "tickets.json", "r", encoding="utf-8") as f:
    tickets = json.load(f)

print(f"Loaded {len(tickets)} tickets")

db.tickets.insert_many(tickets)

print("✅ Tickets inserted")

# -------------------------------------------------
# Load Disputes
# -------------------------------------------------
with open(DATA / "disputes.json", "r", encoding="utf-8") as f:
    disputes = json.load(f)

print(f"Loaded {len(disputes)} disputes")

db.disputes.insert_many(disputes)

print("✅ Disputes inserted")

print("\n MongoDB Seeded Successfully!")

