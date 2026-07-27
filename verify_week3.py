import sys
import json
import uuid
import datetime
from starlette.testclient import TestClient
from starlette.middleware import Middleware

sys.path.insert(0, "d:\\OneDrive\\Desktop\\paydesk-mcp")

from mcp_app import mcp
from security.auth import generate_token, validate_token, resolve_caller, BearerAuthMiddleware
import jwt

# Import and register all tools, resources, and prompts
from tools.postgres_tools import *
from tools.mongo_tools import *
from tools.redis_tools import *
from tools.summary_tools import *
from resources.merchant_resources import register_resources
register_resources(mcp)
from prompts.merchant_prompts import register_prompts
register_prompts(mcp)

# Helper to format output
def pretty_print(name, data):
    print(f"=== {name} ===")
    if isinstance(data, dict) or isinstance(data, list):
        print(json.dumps(data, indent=2, default=str))
    else:
        print(data)
    print()

def establish_session(client, token: str) -> str:
    """
    Establish a session by sending an initialize request and return the session ID.
    """
    init_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "test-client",
                "version": "1.0"
            }
        },
        "id": 1
    }
    headers = {"Authorization": f"Bearer {token}"}
    res = client.post("/mcp", json=init_request, headers=headers)
    if res.status_code != 200:
        raise ValueError(f"Failed to initialize session: {res.status_code} - {res.text}")
    
    session_id = res.headers.get("mcp-session-id")
    if not session_id:
        raise ValueError(f"No session ID returned in headers: {res.headers}")
    return session_id

def main():
    print("============================================================")
    print("1. TESTING JWT TOKEN GENERATION & VALIDATION")
    print("============================================================")
    
    # Test valid token
    token_valid = generate_token("MER-1005")
    caller_valid = validate_token(token_valid)
    print(f"Valid token for MER-1005: {token_valid[:20]}... -> resolved: {caller_valid}")
    
    # Test expired token
    token_expired = generate_token("MER-1005", expires_in_seconds=-10)
    try:
        validate_token(token_expired)
        print("FAIL: Expired token was accepted!")
    except ValueError as e:
        print(f"SUCCESS: Expired token rejected: {e}")
        
    # Test malformed token
    try:
        validate_token("this.is.malformed")
        print("FAIL: Malformed token was accepted!")
    except ValueError as e:
        print(f"SUCCESS: Malformed token rejected: {e}")

    print("============================================================")
    print("2. TESTING HTTP BEARER AUTHENTICATION MIDDLEWARE")
    print("============================================================")
    
    # Instantiate app with BearerAuthMiddleware and json_response=True
    app = mcp.http_app(
        transport="http",
        json_response=True,
        middleware=[Middleware(BearerAuthMiddleware)]
    )
    
    # Initialize JSON-RPC payload for tools/call
    json_rpc_request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "get_merchant_balance",
            "arguments": {
                "merchant_id": "MER-1005"
            }
        },
        "id": 2
    }

    # Case A: Request without Authorization header
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        res_no_auth = client.post("/mcp", json=json_rpc_request)
        pretty_print("Case A: No Authorization Header (Expected: 401)", {
            "status_code": res_no_auth.status_code,
            "body": res_no_auth.json() if res_no_auth.status_code == 401 else res_no_auth.text
        })
        
    # Case B: Request with malformed Authorization header
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        res_malformed = client.post("/mcp", json=json_rpc_request, headers={"Authorization": "Bearer invalid-jwt-signature"})
        pretty_print("Case B: Malformed Token (Expected: 401)", {
            "status_code": res_malformed.status_code,
            "body": res_malformed.json() if res_malformed.status_code == 401 else res_malformed.text
        })
        
    # Case C: Request with expired token
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        res_expired = client.post("/mcp", json=json_rpc_request, headers={"Authorization": f"Bearer {token_expired}"})
        pretty_print("Case C: Expired Token (Expected: 401)", {
            "status_code": res_expired.status_code,
            "body": res_expired.json() if res_expired.status_code == 401 else res_expired.text
        })

    # Case D: Request with valid token for MER-1005 (Requires session ID)
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        # 1. Establish session
        session_id = establish_session(client, token_valid)
        # 2. Call tool with session ID and Auth headers
        res_valid_mer = client.post(
            "/mcp", 
            json=json_rpc_request, 
            headers={
                "Authorization": f"Bearer {token_valid}",
                "mcp-session-id": session_id
            }
        )
        pretty_print("Case D: Valid Token for MER-1005 (Expected: 200 with result)", {
            "status_code": res_valid_mer.status_code,
            "body": res_valid_mer.json()
        })

    print("============================================================")
    print("3. TESTING CONTEXT-BASED AUTHORIZATION & MER-SCOPING OVER HTTPS")
    print("============================================================")

    # Case A: Merchant trying to bypass scoping (passes merchant_id="MER-1001" but is authenticated as "MER-1005")
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token_valid)
        json_rpc_bypass = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "get_merchant_balance",
                "arguments": {
                    "merchant_id": "MER-1001"
                }
            },
            "id": 3
        }
        res_bypass = client.post(
            "/mcp", 
            json=json_rpc_bypass, 
            headers={
                "Authorization": f"Bearer {token_valid}",
                "mcp-session-id": session_id
            }
        )
        pretty_print("Case A: Scoping Override Check (Should return MER-1005 balance despite requesting MER-1001)", {
            "status_code": res_bypass.status_code,
            "body": res_bypass.json()
        })

    # Case B: Admin accessing balance (ADM-01 is authorized and has ledger:read scope)
    token_admin = generate_token("ADM-01")
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token_admin)
        res_admin = client.post(
            "/mcp", 
            json=json_rpc_request, 
            headers={
                "Authorization": f"Bearer {token_admin}",
                "mcp-session-id": session_id
            }
        )
        pretty_print("Case B: Admin access (ADM-01) (Expected: 200 with MER-1005 balance)", {
            "status_code": res_admin.status_code,
            "body": res_admin.json()
        })

    # Case C: Support Agent accessing balance (AGT-01 is support but lacks ledger:read scope)
    token_agent = generate_token("AGT-01")
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token_agent)
        res_agent = client.post(
            "/mcp", 
            json=json_rpc_request, 
            headers={
                "Authorization": f"Bearer {token_agent}",
                "mcp-session-id": session_id
            }
        )
        pretty_print("Case C: Support Agent access (AGT-01) (Expected: 200 with map containing Unauthorized error)", {
            "status_code": res_agent.status_code,
            "body": res_agent.json()
        })

    # Case D: Merchant accessing another merchant's transactions (MER-1006 calling status for MER-1013's TXN-20001)
    token_mer_1006 = generate_token("MER-1006")
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token_mer_1006)
        json_rpc_txn = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "get_transaction_status",
                "arguments": {
                    "txn_id": "TXN-20001"
                }
            },
            "id": 4
        }
        res_txn_denied = client.post(
            "/mcp", 
            json=json_rpc_txn, 
            headers={
                "Authorization": f"Bearer {token_mer_1006}",
                "mcp-session-id": session_id
            }
        )
        pretty_print("Case D: Merchant cross-scoping transaction (Expected: Unauthorized error payload)", {
            "status_code": res_txn_denied.status_code,
            "body": res_txn_denied.json()
        })

    print("============================================================")
    print("4. TESTING AUDIT LOGGING FOR HTTPS REQUESTS")
    print("============================================================")
    
    # Query audit logs for MER-1005 tool execution (requires ADM-01 token)
    with TestClient(app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token_admin)
        json_rpc_logs = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "get_audit_logs",
                "arguments": {
                    "target_caller_id": "MER-1005",
                    "limit": 1
                }
            },
            "id": 5
        }
        res_logs = client.post(
            "/mcp", 
            json=json_rpc_logs, 
            headers={
                "Authorization": f"Bearer {token_admin}",
                "mcp-session-id": session_id
            }
        )
        pretty_print("Audit Logs from MongoDB for MER-1005 actions (Requested by ADM-01)", {
            "status_code": res_logs.status_code,
            "body": res_logs.json()
        })

if __name__ == "__main__":
    main()
