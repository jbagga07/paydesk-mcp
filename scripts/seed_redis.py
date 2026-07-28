# redis library to connect to Redis
import redis

# json library to read json
import json

# pathlib library to work with file paths
from pathlib import Path


# -------------------------------------------------
# Connect to Redis
# -------------------------------------------------
try:
    r = redis.Redis(
        host="localhost",
        port=6379,
        db=0,
        decode_responses=True
    )

    r.ping()
    print("✅ Redis is running")

except redis.ConnectionError:
    print("❌ Redis is not running")
    exit()


# -------------------------------------------------
# Find the project root directory
# -------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# Path to data/redis
DATA = BASE_DIR / "data" / "redis"


# -------------------------------------------------
# Clear old data
# -------------------------------------------------
r.flushdb()
print("Old Redis data cleared")


# -------------------------------------------------
# Helper function
# Converts every value to string so Redis Hash accepts it
# -------------------------------------------------
def convert_to_strings(data):
    converted = {}

    for key, value in data.items():

        if isinstance(value, list):
            # Store lists as JSON strings
            converted[key] = json.dumps(value)

        elif isinstance(value, bool):
            converted[key] = str(value)

        else:
            converted[key] = str(value)

    return converted


# -------------------------------------------------
# Load Merchants
# -------------------------------------------------
with open(DATA / "merchants.json", "r", encoding="utf-8") as f:
    merchants = json.load(f)

print(f"Loaded {len(merchants)} merchants")

for merchant in merchants:

    merchant_id = merchant["merchant_id"]

    r.hset(
        f"merchant:{merchant_id}",
        mapping=convert_to_strings(merchant)
    )

print("✅ Merchants inserted")


# -------------------------------------------------
# Load Admins
# -------------------------------------------------
with open(DATA / "admins.json", "r", encoding="utf-8") as f:
    admins = json.load(f)

print(f"Loaded {len(admins)} admins")

for admin in admins:

    user_id = admin["user_id"]

    r.hset(
        f"admin:{user_id}",
        mapping=convert_to_strings(admin)
    )

print("✅ Admins inserted")


print("\n🎉 Redis Seeded Successfully!")