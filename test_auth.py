import pytest
from security.auth import resolve_caller

def test_resolve_valid_callers():
    test_callers = [
        "MER-1001",
        "MER-1005",
        "AGT-01",
        "ADM-01",
        "FIN-01"
    ]
    for caller in test_callers:
        context = resolve_caller(caller)
        assert context is not None
        assert context.caller_id == caller

def test_resolve_invalid_caller():
    with pytest.raises(Exception):
        resolve_caller("INVALID")