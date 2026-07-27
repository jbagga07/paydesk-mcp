import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DATABASE = os.getenv("POSTGRES_DATABASE")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")

# Startup Validation
missing_pg = []
if not POSTGRES_HOST:
    missing_pg.append("POSTGRES_HOST")
if not POSTGRES_DATABASE:
    missing_pg.append("POSTGRES_DATABASE")
if not POSTGRES_USER:
    missing_pg.append("POSTGRES_USER")
if not POSTGRES_PASSWORD:
    missing_pg.append("POSTGRES_PASSWORD")

if missing_pg:
    raise ValueError(f"Startup Error: Missing required PostgreSQL environment variable(s): {', '.join(missing_pg)}")

try:
    port_val = int(POSTGRES_PORT)
except ValueError:
    raise ValueError(f"Startup Error: Environment variable 'POSTGRES_PORT' must be an integer, got '{POSTGRES_PORT}'")


def get_connection():
    return psycopg.connect(
        host=POSTGRES_HOST,
        port=port_val,
        dbname=POSTGRES_DATABASE,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD
    )