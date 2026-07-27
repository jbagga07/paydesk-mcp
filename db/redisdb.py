import os
import redis
from dotenv import load_dotenv

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
REDIS_DB = os.getenv("REDIS_DB", "0")
REDIS_USERNAME = os.getenv("REDIS_USERNAME") or None
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

# Startup Validation
if not REDIS_HOST:
    raise ValueError("Startup Error: Environment variable 'REDIS_HOST' is required but missing.")
if not REDIS_PORT:
    raise ValueError("Startup Error: Environment variable 'REDIS_PORT' is required but missing.")

try:
    port_val = int(REDIS_PORT)
except ValueError:
    raise ValueError(f"Startup Error: Environment variable 'REDIS_PORT' must be an integer, got '{REDIS_PORT}'")

try:
    db_val = int(REDIS_DB)
except ValueError:
    raise ValueError(f"Startup Error: Environment variable 'REDIS_DB' must be an integer, got '{REDIS_DB}'")

client = redis.Redis(
    host=REDIS_HOST,
    port=port_val,
    db=db_val,
    username=REDIS_USERNAME,
    password=REDIS_PASSWORD,
    decode_responses=True
)


def get_redis():
    return client