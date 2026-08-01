import sys
import json
import uuid
import pytest
from tools.postgres_tools import get_merchant_balance
from tools.mongo_tools import (
    get_transaction_status,
    create_support_ticket,
    get_audit_logs
)
from db.mongodb import get_db
from security.auth import authenticated_as

# 1. TESTING MERCHANT SCOPING & MODEL TRUST LAYER

def test_merchant_scoping_own_balance():
    with authenticated_as("MER-1005"):
        res = get_merchant_balance(merchant_id="MER-1005")
    assert res.get("merchant_id") == "MER-1005"
    assert "balance" in res

def test_merchant_scoping_override():
    with authenticated_as("MER-1005"):
        res = get_merchant_balance(merchant_id="MER-1001")
    assert res.get("merchant_id") == "MER-1005"

def test_admin_authorized_ledger_read():
    with authenticated_as("ADM-01"):
        res = get_merchant_balance(merchant_id="MER-1005")
    assert res.get("merchant_id") == "MER-1005"
    assert "balance" in res

def test_support_agent_unauthorized_ledger_read():
    with authenticated_as("AGT-01"):
        res = get_merchant_balance(merchant_id="MER-1005")
    assert "error" in res
    assert "Unauthorized" in res["error"]

def test_merchant_own_transaction_authorized():
    with authenticated_as("MER-1013"):
        res = get_transaction_status(txn_id="TXN-20001")
    assert res.get("txn_id") == "TXN-20001"
    assert res.get("merchant_id") == "MER-1013"

def test_merchant_cross_scoping_transaction_denied():
    with authenticated_as("MER-1006"):
        res = get_transaction_status(txn_id="TXN-20001")
    assert "error" in res
    assert "Unauthorized" in res["error"]

# 2. TESTING GUARDED WRITE TOOL (SUPPORT TICKET)

def test_ticket_approval_gate_and_idempotency():
    request_id = f"REQ-TEST-{uuid.uuid4().hex[:6]}"
    
    # A. approved=False
    with authenticated_as("MER-1006"):
        res_gate = create_support_ticket(
            merchant_id="MER-1006",
            title="Payment failure on checkout page",
            description="Customers reporting UPI payments are failing repeatedly.",
            request_id=request_id,
            approved=False
        )
    assert res_gate.get("status") == "AWAITING_APPROVAL"
    
    # B. approved=True
    with authenticated_as("MER-1006"):
        res_create = create_support_ticket(
            merchant_id="MER-1006",
            title="Payment failure on checkout page",
            description="Customers reporting UPI payments are failing repeatedly.",
            request_id=request_id,
            approved=True
        )
    assert res_create.get("message") == "Ticket created successfully."
    assert "ticket" in res_create
    
    # C. Idempotency (same request_id)
    with authenticated_as("MER-1006"):
        res_idemp = create_support_ticket(
            merchant_id="MER-1006",
            title="Payment failure on checkout page",
            description="Customers reporting UPI payments are failing repeatedly.",
            request_id=request_id,
            approved=True
        )
    assert "already exists" in res_idemp.get("message", "")

def test_ticket_invalid_merchant():
    with authenticated_as("ADM-01"):
        res = create_support_ticket(
            merchant_id="MER-9999",
            title="Admin ticket",
            description="Creating a ticket for a non-existent merchant.",
            request_id=f"REQ-TEST-{uuid.uuid4().hex[:6]}",
            approved=True
        )
    assert "error" in res
    assert "invalid" in res["error"].lower() or "does not exist" in res["error"].lower()

def test_ticket_oversized_title():
    with authenticated_as("MER-1006"):
        res = create_support_ticket(
            merchant_id="MER-1006",
            title="A" * 105,
            description="Valid description.",
            request_id=f"REQ-TEST-{uuid.uuid4().hex[:6]}",
            approved=True
        )
    assert "error" in res
    assert "exceeds maximum length" in res["error"].lower()

# 3. TESTING CENTRALIZED AUDIT LOGGING

def test_audit_logs_access():
    with authenticated_as("ADM-01"):
        res = get_audit_logs(target_caller_id="MER-1006", limit=5)
    assert "logs" in res
    assert isinstance(res["logs"], list)

    with authenticated_as("MER-1006"):
        res = get_audit_logs(limit=5)
    assert "error" in res
    assert "Unauthorized" in res["error"]

# 4. TESTING PROMPT INJECTION DEFENSE

def test_prompt_injection_defense():
    db = get_db()
    ticket_mal = db.tickets.find_one({"request_id": "REQ-MAL-01"}, {"_id": 0})
    assert ticket_mal is not None
    assert ticket_mal["request_id"] == "REQ-MAL-01"
    assert "Ignore previous instructions" in ticket_mal["description"]

if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
