from db.mongodb import get_db

def test_mongodb_count():
    db = get_db()
    count = db.transactions.count_documents({})
    assert isinstance(count, int)
    assert count >= 0