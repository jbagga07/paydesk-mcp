import sys
import json
import uuid

# Helper to format output
def pretty_print(name, data):
    print(f"=== {name} ===")
    print(json.dumps(data, indent=2, default=str))
    print()

def main():
    sys.path.insert(0, "d:\\OneDrive\\Desktop\\paydesk-mcp")
    
    from tools.postgres_tools import get_merchant_balance
    from tools.mongo_tools import (
        get_transaction_status,
        get_recent_transactions,
        create_support_ticket,
        get_audit_logs
    )
    from db.mongodb import get_db
    from security.auth import authenticated_as
    
    db = get_db()
    
    print("============================================================")
    print("1. TESTING MERCHANT SCOPING & MODEL TRUST LAYER")
    print("============================================================")
    
    # Test case A: Merchant calling balance for itself
    # MER-1005 is allowed to view MER-1005
    with authenticated_as("MER-1005"):
        res_a = get_merchant_balance(merchant_id="MER-1005")
    pretty_print("Merchant MER-1005 requesting its own balance", res_a)
    
    # Test case B: Merchant calling balance but passing different merchant_id
    # Scoping should override and return MER-1005 balance (ignores model trusted merchant_id)
    with authenticated_as("MER-1005"):
        res_b = get_merchant_balance(merchant_id="MER-1001")
    pretty_print("Merchant MER-1005 requesting MER-1001 balance (override check)", res_b)
    
    # Test case C: Admin with proper scope accessing merchant
    # ADM-01 is admin and has ledger:read scope
    with authenticated_as("ADM-01"):
        res_c = get_merchant_balance(merchant_id="MER-1005")
    pretty_print("Admin ADM-01 requesting MER-1005 balance (authorized)", res_c)

    # Test case D: Admin WITHOUT proper scope accessing merchant
    # AGT-01 is support agent, has txn:read/ticket:read but NOT ledger:read
    with authenticated_as("AGT-01"):
        res_d = get_merchant_balance(merchant_id="MER-1005")
    pretty_print("Support Agent AGT-01 requesting MER-1005 balance (unauthorized)", res_d)

    # Test case E: Cross-merchant transaction read scoping
    # MER-1013 owns TXN-20001. MER-1006 does not.
    with authenticated_as("MER-1013"):
        res_e1 = get_transaction_status(txn_id="TXN-20001")
    pretty_print("Merchant MER-1013 accessing own transaction TXN-20001 (authorized)", res_e1)
    
    with authenticated_as("MER-1006"):
        res_e2 = get_transaction_status(txn_id="TXN-20001")
    pretty_print("Merchant MER-1006 accessing MER-1013's transaction TXN-20001 (denied)", res_e2)

    print("============================================================")
    print("2. TESTING GUARDED WRITE TOOL (SUPPORT TICKET)")
    print("============================================================")
    
    request_id = f"REQ-TEST-{uuid.uuid4().hex[:6]}"
    
    # Test case A: Approval gate (approved=False)
    with authenticated_as("MER-1006"):
        res_gate = create_support_ticket(
            merchant_id="MER-1006",
            title="Payment failure on checkout page",
            description="Customers reporting UPI payments are failing repeatedly.",
            request_id=request_id,
            approved=False
        )
    pretty_print("Ticket creation with approved=False (Awaiting Approval)", res_gate)
    
    # Test case B: Creation with approved=True
    with authenticated_as("MER-1006"):
        res_create = create_support_ticket(
            merchant_id="MER-1006",
            title="Payment failure on checkout page",
            description="Customers reporting UPI payments are failing repeatedly.",
            request_id=request_id,
            approved=True
        )
    pretty_print("Ticket creation with approved=True (Success)", res_create)
    
    # Test case C: Idempotency (same request_id)
    with authenticated_as("MER-1006"):
        res_idemp = create_support_ticket(
            merchant_id="MER-1006",
            title="Payment failure on checkout page",
            description="Customers reporting UPI payments are failing repeatedly.",
            request_id=request_id,
            approved=True
        )
    pretty_print("Ticket creation with same request_id (Idempotency Return)", res_idemp)

    # Test case D: Validation - Invalid merchant
    with authenticated_as("ADM-01"):
        res_invalid_mer = create_support_ticket(
            merchant_id="MER-9999",
            title="Admin ticket",
            description="Creating a ticket for a non-existent merchant.",
            request_id=f"REQ-TEST-{uuid.uuid4().hex[:6]}",
            approved=True
        )
    pretty_print("Ticket creation with invalid merchant MER-9999 (Rejected)", res_invalid_mer)

    # Test case E: Validation - Oversized title
    with authenticated_as("MER-1006"):
        res_oversized = create_support_ticket(
            merchant_id="MER-1006",
            title="A" * 105,
            description="Valid description.",
            request_id=f"REQ-TEST-{uuid.uuid4().hex[:6]}",
            approved=True
        )
    pretty_print("Ticket creation with oversized title (Rejected)", res_oversized)

    print("============================================================")
    print("3. TESTING CENTRALIZED AUDIT LOGGING")
    print("============================================================")
    
    # Admins can retrieve audit logs. ADM-01 is ADMIN.
    with authenticated_as("ADM-01"):
        res_logs = get_audit_logs(target_caller_id="MER-1006", limit=5)
    pretty_print("Admin ADM-01 retrieving audit logs for MER-1006", res_logs)
    
    # Non-admins should be rejected from reading audit logs
    with authenticated_as("MER-1006"):
        res_logs_denied = get_audit_logs(limit=5)
    pretty_print("Merchant MER-1006 attempting to retrieve audit logs (Denied)", res_logs_denied)

    print("============================================================")
    print("4. TESTING PROMPT INJECTION DEFENSE")
    print("============================================================")
    
    # Query one of the malicious tickets we seeded in MongoDB
    ticket_mal = db.tickets.find_one({"request_id": "REQ-MAL-01"}, {"_id": 0})
    pretty_print("Seeded prompt injection ticket in database (treated strictly as plain text)", ticket_mal)

if __name__ == "__main__":
    main()
