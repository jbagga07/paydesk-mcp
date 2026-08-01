from db.postgres import get_connection

def test_postgres_count():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ledger;")
            count = cur.fetchone()
            assert count is not None
            assert isinstance(count[0], int)
            assert count[0] >= 0
    finally:
        conn.close()