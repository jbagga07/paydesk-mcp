from security.auth import CallerContext
from typing import Optional

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