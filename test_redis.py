from db.redisdb import get_redis

r = get_redis()

print(r.ping())