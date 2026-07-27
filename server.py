import os
from dotenv import load_dotenv
from mcp_app import mcp
from starlette.middleware import Middleware
from security.auth import get_or_create_ssl_certs, BearerAuthMiddleware

load_dotenv()

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = os.getenv("MCP_PORT", "8000")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

try:
    port_val = int(MCP_PORT)
except ValueError:
    raise ValueError(f"Startup Error: Environment variable 'MCP_PORT' must be an integer, got '{MCP_PORT}'")

# Import all tools
from tools.postgres_tools import *
from tools.mongo_tools import *
from tools.redis_tools import *
from tools.summary_tools import *
from resources.merchant_resources import register_resources

register_resources(mcp)

from prompts.merchant_prompts import register_prompts
register_prompts(mcp)

if __name__ == "__main__":
    key_file, cert_file = get_or_create_ssl_certs()
    
    print(f"Starting PayDesk MCP server on {MCP_HOST}:{port_val} with log level '{LOG_LEVEL}'...")
    mcp.run(
        transport="http",
        host=MCP_HOST,
        port=port_val,
        middleware=[Middleware(BearerAuthMiddleware)],
        uvicorn_config={
            "ssl_keyfile": key_file,
            "ssl_certfile": cert_file,
            "log_level": LOG_LEVEL.lower()
        }
    )