import functools
import datetime
import uuid
import sys
import inspect
from typing import Optional
from db.mongodb import get_db

def log_tool_call(
    caller_id: str,
    caller_type: str,
    tool_name: str,
    arguments: dict,
    result: dict,
    correlation_id: str,
    success: bool
):
    """
    Centralized helper to log tool call details into MongoDB.
    """
    try:
        db = get_db()
        log_entry = {
            "caller_id": caller_id,
            "caller_type": caller_type,
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "correlation_id": correlation_id,
            "success": success
        }
        db.audit_logs.insert_one(log_entry)
    except Exception as e:
        print(f"Audit log insertion failed: {e}", file=sys.stderr)


def get_audit_logs(target_caller_id: Optional[str] = None, limit: int = 20) -> list:
    """
    Centralized helper to retrieve audit logs from MongoDB.
    """
    try:
        db = get_db()
        query = {}
        if target_caller_id:
            query["caller_id"] = target_caller_id
        logs = list(
            db.audit_logs.find(query, {"_id": 0})
            .sort("timestamp", -1)
            .limit(limit)
        )
        return logs
    except Exception as e:
        print(f"Failed to retrieve audit logs: {e}", file=sys.stderr)
        return []


def audit_logged(func):
    """
    Decorator to automatically audit log MCP tool execution.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tool_name = func.__name__
        
        # Parse arguments passed to the function
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        arguments = bound.arguments
        
        correlation_id = str(uuid.uuid4())
        
        # Resolve caller from the current authenticated context
        caller_id = "UNKNOWN"
        caller_type = "UNKNOWN"
        try:
            from security.auth import get_current_caller
            context = get_current_caller()
            caller_id = context.caller_id
            caller_type = context.caller_type
        except Exception:
            pass
        
        # Format arguments safely for JSON (stringifying non-serializable objects)
        serializable_args = {}
        for k, v in arguments.items():
            serializable_args[k] = str(v)
            
        success = False
        result = None
        try:
            result = func(*args, **kwargs)
            if isinstance(result, dict) and "error" in result:
                success = False
            else:
                success = True
            return result
        except Exception as e:
            result = {"error": str(e)}
            success = False
            raise
        finally:
            # Perform logging
            log_tool_call(
                caller_id=caller_id,
                caller_type=caller_type,
                tool_name=tool_name,
                arguments=serializable_args,
                result=result,
                correlation_id=correlation_id,
                success=success
            )
            
    return wrapper
