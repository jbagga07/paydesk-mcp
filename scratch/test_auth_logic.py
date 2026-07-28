import functools
import inspect

# Mock security functions
class MockCallerContext:
    def __init__(self, caller_type, merchant_id=None, user_id=None, scopes=None):
        self.caller_type = caller_type
        self.merchant_id = merchant_id
        self.user_id = user_id
        self.scopes = scopes or []

    @property
    def caller_id(self):
        if self.caller_type == "merchant":
            return self.merchant_id or "UNKNOWN"
        return self.user_id or "UNKNOWN"

_current_context = None

def get_current_caller():
    if _current_context is None:
        raise ValueError("Authentication required: No active caller context.")
    return _current_context

def is_authorized(context, merchant_id, required_scope):
    if context.caller_type == "merchant":
        return context.merchant_id == merchant_id
    if context.caller_type == "admin":
        return required_scope in context.scopes
    return False

# The decorator implementation
def require_merchant_auth(required_scope: str):
    """
    Decorator to enforce authentication and authorization for merchant resources.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                # 1. Authenticate caller
                context = get_current_caller()
                
                # 2. Extract merchant_id from arguments
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                
                merchant_id = bound_args.arguments.get("merchant_id")
                
                # 3. If caller is merchant, ignore URI's merchant_id and use context.merchant_id
                if context.caller_type == "merchant":
                    merchant_id = context.merchant_id
                
                # 4. Authorize caller
                if not is_authorized(context, merchant_id, required_scope=required_scope):
                    return {
                        "error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to access merchant '{merchant_id}' with scope '{required_scope}'."
                    }
                
                # Update bound arguments with the resolved merchant_id
                bound_args.arguments["merchant_id"] = merchant_id
                
                # 5. Call the actual resource function and return its response
                return func(*bound_args.args, **bound_args.kwargs)
                
            except Exception as e:
                return {"error": str(e)}
        return wrapper
    return decorator

# Test targets
@require_merchant_auth("txn:read")
def test_resource(merchant_id: str):
    """Docstring check."""
    if merchant_id == "FAIL_DB":
        raise RuntimeError("Database connection lost")
    return {"status": "success", "merchant_id": merchant_id}

# Run tests
print("Docstring preserved:", test_resource.__doc__)
print("Signature preserved:", inspect.signature(test_resource))

# Case 1: Unauthenticated
_current_context = None
print("Unauthenticated:", test_resource("MER-123"))

# Case 2: Authenticated Merchant trying to access own data
_current_context = MockCallerContext("merchant", "MER-1005")
print("Merchant own data:", test_resource("MER-1005"))

# Case 3: Authenticated Merchant trying to bypass scoping (passes MER-1001)
_current_context = MockCallerContext("merchant", "MER-1005")
print("Merchant bypass attempt:", test_resource("MER-1001"))

# Case 4: Admin authorized
_current_context = MockCallerContext("admin", user_id="ADM-01", scopes=["txn:read"])
print("Admin authorized:", test_resource("MER-1001"))

# Case 5: Admin unauthorized scope
_current_context = MockCallerContext("admin", user_id="ADM-01", scopes=["ledger:read"])
print("Admin unauthorized scope:", test_resource("MER-1001"))

# Case 6: Handler raises runtime error
_current_context = MockCallerContext("merchant", "MER-1005")
print("Handler crash catch:", test_resource("FAIL_DB"))
