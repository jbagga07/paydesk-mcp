from db.mongodb import get_db

db = get_db()

count = db.transactions.count_documents({})

print("Transactions:", count)