from security.auth import resolve_caller
from security.scope import get_allowed_merchant_ids

def test_allowed_merchant_ids():
    callers = [
        "MER-1001",
        "MER-1005",
        "AGT-01",
        "ADM-01",
        "FIN-01"
    ]

    for caller in callers:
        context = resolve_caller(caller)
        allowed_ids = get_allowed_merchant_ids(context)
        if context.caller_type == "merchant":
            assert allowed_ids == [context.merchant_id]
        else:
            # Admins with global access
            assert allowed_ids is None