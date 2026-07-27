from db.redisdb import get_redis

r = get_redis()

merchant = r.hgetall("merchant:MER-1005")

print(merchant)