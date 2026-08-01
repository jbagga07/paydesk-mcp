from db.redisdb import get_redis

def test_redis_ping():
    r = get_redis()
    assert r.ping() is True