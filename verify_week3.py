import sys
import json
import uuid
import datetime
import pytest
from starlette.testclient import TestClient
from starlette.middleware import Middleware
import jwt

sys.path.insert(0, "d:\\OneDrive\\Desktop\\paydesk-mcp")

from mcp_app import mcp
from security.auth import generate_token, validate_token, resolve_caller, BearerAuthMiddleware, JWT_SECRET

# Import and register all tools, resources, and prompts
from tools.postgres_tools import *
from tools.mongo_tools import *
from tools.redis_tools import *
from tools.summary_tools import *
from resources.merchant_resources import register_resources
register_resources(mcp)
from prompts.merchant_prompts import register_prompts
register_prompts(mcp)

def establish_session(client, token: str) -> str:
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

# 1. TESTING JWT TOKEN GENERATION & VALIDATION

def test_jwt_token_validation():
    # Valid token
    token_valid = generate_token("MER-1005")
    caller_valid = validate_token(token_valid)
    assert caller_valid == "MER-1005"
    
    # Expired token
    token_expired = generate_token("MER-1005", expires_in_seconds=-10)
    with pytest.raises(ValueError) as excinfo:
        validate_token(token_expired)
    assert "expired" in str(excinfo.value).lower()
        
    # Malformed token
    with pytest.raises(ValueError) as excinfo2:
        validate_token("this.is.malformed")
    assert "invalid" in str(excinfo2.value).lower() or "malformed" in str(excinfo2.value).lower()

# 2. TESTING HTTP BEARER AUTHENTICATION MIDDLEWARE

@pytest.fixture
def test_app():
    return mcp.http_app(
        transport="http",
        json_response=True,
        middleware=[Middleware(BearerAuthMiddleware)]
    )

@pytest.fixture
def json_rpc_request():
    return {
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

def test_middleware_no_auth_header(test_app, json_rpc_request):
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        res = client.post("/mcp", json=json_rpc_request)
    assert res.status_code == 401
    assert res.json().get("error") == "Missing Authorization header"

def test_middleware_malformed_token(test_app, json_rpc_request):
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        res = client.post("/mcp", json=json_rpc_request, headers={"Authorization": "Bearer invalid-jwt-signature"})
    assert res.status_code == 401
    assert "Invalid token" in res.json().get("error", "")

def test_middleware_wrong_audience(test_app, json_rpc_request):
    wrong_audience_payload = {
        "sub": "MER-1001",
        "aud": "admin-dashboard",
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    }
    token = jwt.encode(wrong_audience_payload, JWT_SECRET, algorithm="HS256")
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        res = client.post("/mcp", json=json_rpc_request, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert "Invalid audience" in res.json().get("error", "")

def test_middleware_valid_token(test_app, json_rpc_request):
    token = generate_token("MER-1005")
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token)
        res = client.post(
            "/mcp", 
            json=json_rpc_request, 
            headers={
                "Authorization": f"Bearer {token}",
                "mcp-session-id": session_id
            }
        )
    assert res.status_code == 200
    res_json = res.json()
    assert "result" in res_json
    assert "error" not in res_json["result"].get("structuredContent", {})

# 3. TESTING CONTEXT-BASED AUTHORIZATION & MER-SCOPING OVER HTTPS

def test_cross_scoping_override(test_app):
    token = generate_token("MER-1005")
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
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token)
        res = client.post(
            "/mcp", 
            json=json_rpc_bypass, 
            headers={
                "Authorization": f"Bearer {token}",
                "mcp-session-id": session_id
            }
        )
    assert res.status_code == 200
    res_json = res.json()
    assert res_json["result"]["structuredContent"]["merchant_id"] == "MER-1005"

def test_admin_access_authorized(test_app, json_rpc_request):
    token = generate_token("ADM-01")
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token)
        res = client.post(
            "/mcp", 
            json=json_rpc_request, 
            headers={
                "Authorization": f"Bearer {token}",
                "mcp-session-id": session_id
            }
        )
    assert res.status_code == 200
    res_json = res.json()
    assert res_json["result"]["structuredContent"]["merchant_id"] == "MER-1005"

def test_agent_access_unauthorized(test_app, json_rpc_request):
    token = generate_token("AGT-01")
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token)
        res = client.post(
            "/mcp", 
            json=json_rpc_request, 
            headers={
                "Authorization": f"Bearer {token}",
                "mcp-session-id": session_id
            }
        )
    assert res.status_code == 200
    res_json = res.json()
    assert "error" in res_json["result"]["structuredContent"]
    assert "Unauthorized" in res_json["result"]["structuredContent"]["error"]

def test_merchant_cross_scoping_transaction_denied(test_app):
    token = generate_token("MER-1006")
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
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token)
        res = client.post(
            "/mcp", 
            json=json_rpc_txn, 
            headers={
                "Authorization": f"Bearer {token}",
                "mcp-session-id": session_id
            }
        )
    assert res.status_code == 200
    res_json = res.json()
    assert "error" in res_json["result"]["structuredContent"]
    assert "Unauthorized" in res_json["result"]["structuredContent"]["error"]

# 4. TESTING AUDIT LOGGING FOR HTTPS REQUESTS

def test_audit_logs_https(test_app):
    token_admin = generate_token("ADM-01")
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
    with TestClient(test_app) as client:
        client.headers.update({"Accept": "application/json"})
        session_id = establish_session(client, token_admin)
        res = client.post(
            "/mcp", 
            json=json_rpc_logs, 
            headers={
                "Authorization": f"Bearer {token_admin}",
                "mcp-session-id": session_id
            }
        )
    assert res.status_code == 200
    res_json = res.json()
    assert "logs" in res_json["result"]["structuredContent"]

if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
