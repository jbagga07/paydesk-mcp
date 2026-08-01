from db.redisdb import get_redis

def test_redis_merchant_keys():
    r = get_redis()
    merchant = r.hgetall("merchant:MER-1005")
    assert merchant is not None
    assert isinstance(merchant, dict)
    assert len(merchant) > 0
    assert "name" in merchant or b"name" in merchant