import functools
import inspect
import asyncio
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Test")

def require_auth(scope):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator

@mcp.resource("merchant://summary/{merchant_id}")
@require_auth("txn:read")
def merchant_summary_resource(merchant_id: str):
    return {"merchant_id": merchant_id}

async def main():
    resources = await mcp.list_resources()
    print("Resources:", resources)
    
    # Try calling the resource read
    # In fastmcp, read_resource might be an async method
    try:
        res = await mcp.read_resource("merchant://summary/123")
        print("Read result:", res)
    except Exception as e:
        print("Read error:", e)

asyncio.run(main())
