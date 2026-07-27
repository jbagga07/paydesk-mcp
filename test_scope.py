from security.auth import resolve_caller
from security.scope import get_allowed_merchant_ids

callers = [
    "MER-1001",
    "MER-1005",
    "AGT-01",
    "ADM-01",
    "FIN-01"
]

for caller in callers:
    context = resolve_caller(caller)

    print("=" * 60)
    print(caller)
    print(get_allowed_merchant_ids(context))