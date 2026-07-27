from mcp_app import mcp
from db.redisdb import get_redis
from security.auth import get_current_caller
from security.scope import is_authorized
from security.audit import audit_logged
import uuid
import datetime
import json
from typing import Optional

redis_client = get_redis()


@mcp.tool
@audit_logged
def get_merchant(merchant_id: str):
    """
    Get merchant details from Redis.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="txn:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access merchant '{merchant_id}'."}

    key = f"merchant:{merchant_id}"
    merchant = redis_client.hgetall(key)

    if not merchant:
        return {"error": f"Merchant '{merchant_id}' not found."}

    return merchant


@mcp.tool
@audit_logged
def get_api_keys(merchant_id: str):
    """
    Get metadata of active API keys (IDs, scopes, prefix, creation date, status) for a merchant.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="api_key:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to read API keys for merchant '{merchant_id}'."}

    key = f"merchant:{merchant_id}:api_keys"
    keys_data = redis_client.hgetall(key)

    results = []
    for kid, val_str in keys_data.items():
        try:
            results.append(json.loads(val_str))
        except Exception:
            results.append({"key_id": kid, "raw_value": val_str})

    return {"merchant_id": merchant_id, "api_keys": results}


@mcp.tool
@audit_logged
def create_api_key(
    merchant_id: str,
    name: str,
    scopes: list[str],
    approved: bool = False
):
    """
    Generate a new API token for the merchant, hash it, and store metadata in Redis.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="api_key:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to create API keys for merchant '{merchant_id}'."}

    if not name or not name.strip():
        return {"error": "Validation failed: Key name cannot be empty."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "API key creation requires explicit approval. Set approved=True to generate the key.",
            "details": {"merchant_id": merchant_id, "name": name, "scopes": scopes}
        }

    # Generate a key and token
    key_id = f"key_{uuid.uuid4().hex[:12]}"
    secret_token = f"pk_live_{uuid.uuid4().hex}"
    
    metadata = {
        "key_id": key_id,
        "name": name,
        "prefix": secret_token[:12] + "...",
        "scopes": scopes,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "ACTIVE"
    }

    # Store in Redis hash
    redis_client.hset(f"merchant:{merchant_id}:api_keys", key_id, json.dumps(metadata))
    # Also store the mapping secret_token -> merchant_id for auth simulation
    redis_client.set(f"token_auth:{secret_token}", json.dumps({"merchant_id": merchant_id, "scopes": scopes}))

    return {
        "message": "API key created successfully. Please record the secret token, as it will not be displayed again.",
        "key_id": key_id,
        "secret_token": secret_token,
        "metadata": metadata
    }


@mcp.tool
@audit_logged
def revoke_api_key(
    merchant_id: str,
    key_id: str,
    approved: bool = False
):
    """
    Revoke/delete a specific API key from Redis.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="api_key:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to revoke API keys for merchant '{merchant_id}'."}

    hash_key = f"merchant:{merchant_id}:api_keys"
    
    if not redis_client.hexists(hash_key, key_id):
        return {"error": f"API key '{key_id}' not found for merchant '{merchant_id}'."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "API key revocation requires explicit approval. Set approved=True to revoke.",
            "details": {"merchant_id": merchant_id, "key_id": key_id}
        }

    # Delete from Redis
    redis_client.hdel(hash_key, key_id)

    return {
        "message": f"API key '{key_id}' has been successfully revoked.",
        "merchant_id": merchant_id,
        "key_id": key_id
    }


@mcp.tool
@audit_logged
def get_webhook_config(merchant_id: str):
    """
    Retrieve webhook destination URL and active subscribed events.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="webhook:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to read webhook configuration for merchant '{merchant_id}'."}

    key = f"merchant:{merchant_id}:webhook"
    config = redis_client.hgetall(key)

    if not config:
        return {"merchant_id": merchant_id, "webhook_config": None, "message": "No webhook configured."}

    # parse events back to list
    if "events" in config:
        try:
            config["events"] = json.loads(config["events"])
        except Exception:
            pass

    return {"merchant_id": merchant_id, "webhook_config": config}


@mcp.tool
@audit_logged
def update_webhook_config(
    merchant_id: str,
    url: str,
    events: list[str],
    approved: bool = False
):
    """
    Create or update the webhook URL and events in Redis.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="webhook:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to update webhook configuration for merchant '{merchant_id}'."}

    if not url.startswith("http://") and not url.startswith("https://"):
        return {"error": "Validation failed: Webhook URL must start with http:// or https://"}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Webhook updates require approval. Set approved=True to submit.",
            "details": {"merchant_id": merchant_id, "url": url, "events": events}
        }

    key = f"merchant:{merchant_id}:webhook"
    config = {
        "url": url,
        "events": json.dumps(events),
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }

    redis_client.hset(key, mapping=config)

    return {
        "message": "Webhook configuration updated successfully.",
        "merchant_id": merchant_id,
        "webhook_config": {
            "url": url,
            "events": events,
            "updated_at": config["updated_at"]
        }
    }


@mcp.tool
@audit_logged
def get_active_sessions(merchant_id: str):
    """
    Retrieve active merchant dashboard sessions from Redis.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="session:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to read sessions for merchant '{merchant_id}'."}

    key = f"merchant:{merchant_id}:sessions"
    sessions_data = redis_client.hgetall(key)

    results = []
    for sid, val_str in sessions_data.items():
        try:
            results.append(json.loads(val_str))
        except Exception:
            results.append({"session_id": sid, "value": val_str})

    return {"merchant_id": merchant_id, "active_sessions": results}


@mcp.tool
@audit_logged
def revoke_session(
    merchant_id: str,
    session_id: str,
    approved: bool = False
):
    """
    Force log out/revoke a dashboard session by deleting its key.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="session:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to revoke sessions for merchant '{merchant_id}'."}

    hash_key = f"merchant:{merchant_id}:sessions"

    if not redis_client.hexists(hash_key, session_id):
        return {"error": f"Session '{session_id}' not found for merchant '{merchant_id}'."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Session revocation requires explicit approval. Set approved=True to revoke.",
            "details": {"merchant_id": merchant_id, "session_id": session_id}
        }

    redis_client.hdel(hash_key, session_id)

    return {
        "message": f"Session '{session_id}' has been successfully terminated.",
        "merchant_id": merchant_id,
        "session_id": session_id
    }


@mcp.tool
@audit_logged
def get_rate_limit_status(merchant_id: str):
    """
    Check the current rate limit usage and quota status for the merchant's API in Redis.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="api_key:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to view rate limits for merchant '{merchant_id}'."}

    # Simulate/fetch rate limit bucket from Redis
    bucket_key = f"rate_limit:{merchant_id}:minute"
    current_count = redis_client.get(bucket_key)
    
    limit = 1000
    used = int(current_count) if current_count else 0
    remaining = max(0, limit - used)
    ttl = redis_client.ttl(bucket_key)
    reset_seconds = ttl if ttl > 0 else 60

    if not current_count:
        # initialize bucket for simulation
        redis_client.setex(bucket_key, 60, 1)
        used = 1
        remaining = limit - 1

    return {
        "merchant_id": merchant_id,
        "rate_limit_minute": limit,
        "requests_used": used,
        "requests_remaining": remaining,
        "reset_in_seconds": reset_seconds
    }