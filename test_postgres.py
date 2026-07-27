from db.postgres import get_connection

conn = get_connection()
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM ledger;")

count = cur.fetchone()

print(count)

cur.close()
conn.close()