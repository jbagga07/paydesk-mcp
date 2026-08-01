from security.auth import CallerContext, get_current_caller
from typing import Optional, Any
import functools
import inspect

def can_access_merchant(
    context: CallerContext,
    merchant_id: str,
) -> bool:
    """
    Returns True if the caller is allowed to access the given merchant.
    """

    # Merchant can only access itself
    if context.caller_type == "merchant":
        return context.merchant_id == merchant_id

    # Admin with global access
    if context.caller_type == "admin":
        return context.can_view_all_merchants

    return False

def has_scope(
    context: CallerContext,
    required_scope: str,
) -> bool:
    """
    Returns True if the caller has the required scope.
    """

    return required_scope in context.scopes




def get_allowed_merchant_ids(
    context: CallerContext,
) -> Optional[list[str]]:
    """
    Returns the merchant IDs the caller is allowed to access.

    Returns:
        - [merchant_id] for merchants
        - None for admins with global access
    """

    if context.caller_type == "merchant":
        return [context.merchant_id]

    if context.caller_type == "admin" and context.can_view_all_merchants:
        return None

    return []


def is_authorized(
    context: CallerContext,
    merchant_id: Optional[str] = None,
    required_scope: Optional[str] = None,
    required_scopes: Optional[list[str]] = None
) -> bool:
    """
    Centralized authorization check.
    For merchants: validates they are accessing their own merchant_id.
    For admins: validates they have access to the merchant_id and possess the required_scope(s).
    """
    if merchant_id is not None:
        if not can_access_merchant(context, merchant_id):
            return False

    if context.caller_type == "admin":
        if required_scope is not None:
            if not has_scope(context, required_scope):
                return False
        if required_scopes is not None:
            for scope in required_scopes:
                if not has_scope(context, scope):
                    return False
    return True


def scoped(
    required_scope: Optional[str] = None,
    required_scopes: Optional[list[str]] = None,
    admin_only: bool = False,
    admin_only_msg: Optional[str] = None,
    error_msg: Optional[str] = None
):
    """
    Reusable decorator for authentication, merchant scoping override, and authorization.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                context = get_current_caller()
            except Exception as e:
                return {"error": f"Authentication failed: {str(e)}"}

            if admin_only:
                if context.caller_type != "admin" or context.role != "ADMIN":
                    msg = admin_only_msg or "Unauthorized: Only administrators can execute this action."
                    return {"error": msg}

            # Inspect and bind function parameters to check for merchant_id
            sig = inspect.signature(func)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()

            # Override merchant_id for merchants
            if context.caller_type == "merchant" and "merchant_id" in bound.arguments:
                bound.arguments["merchant_id"] = context.merchant_id

            merchant_id = bound.arguments.get("merchant_id")

            # Check authorization if scope check or merchant_id check is needed
            if not is_authorized(
                context,
                merchant_id=merchant_id,
                required_scope=required_scope,
                required_scopes=required_scopes
            ):
                caller_id = context.caller_id
                if error_msg:
                    msg = error_msg.format(caller_id=caller_id, merchant_id=merchant_id)
                else:
                    msg = f"Unauthorized: Caller '{caller_id}' cannot access merchant '{merchant_id}'."
                return {"error": msg}

            # Invoke target function with overridden/updated parameters
            return func(*bound.args, **bound.kwargs)
        return wrapper
    return decorator