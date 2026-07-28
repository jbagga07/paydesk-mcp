import json
from pathlib import Path

import psycopg2


# -----------------------------------------
# Connect to PostgreSQL
# -----------------------------------------
try:
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        database="paydesk",
        user="postgres",
        password=input("Enter PostgreSQL password: ")
    )

    cur = conn.cursor()
    print("✅ Connected to PostgreSQL")

except Exception as e:
    print("❌ Could not connect")
    print(e)
    exit()


# -----------------------------------------
# Project Paths
# -----------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

DATA = BASE_DIR / "data" / "postgres"

LEDGER_FILE = DATA / "ledger.json"


# -----------------------------------------
# Clear old data
# -----------------------------------------
cur.execute("TRUNCATE TABLE ledger;")
conn.commit()

print("Old ledger data cleared")


# -----------------------------------------
# Load JSON
# -----------------------------------------
with open(LEDGER_FILE, "r", encoding="utf-8") as f:
    ledger = json.load(f)

print(f"Loaded {len(ledger)} ledger entries")


# -----------------------------------------
# Insert records
# -----------------------------------------
insert_query = """
INSERT INTO ledger (
    entry_id,
    txn_id,
    merchant_id,
    settlement_id,
    account,
    direction,
    amount,
    currency,
    posted_at
)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""

for row in ledger:

    cur.execute(
        insert_query,
        (
            row["entry_id"],
            row["txn_id"],
            row["merchant_id"],
            row["settlement_id"],
            row["account"],
            row["direction"],
            row["amount"],
            row["currency"],
            row["posted_at"],
        ),
    )

conn.commit()

print("✅ Ledger inserted successfully")


cur.close()
conn.close()

print("🎉 PostgreSQL Seeded Successfully!")