from security.auth import resolve_caller

test_callers = [
    "MER-1001",
    "MER-1005",
    "AGT-01",
    "ADM-01",
    "FIN-01",
    "INVALID"
]

for caller in test_callers:
    print("=" * 60)
    print(f"Testing caller: {caller}")

    try:
        context = resolve_caller(caller)

        print("Resolved Successfully")
        print(context)

    except Exception as e:
        print(f"Error: {e}")