import sys
import datetime
import uuid
from unittest.mock import MagicMock

# Mock Redis client BEFORE importing tools
import db.redisdb

class MockRedis:
    def __init__(self, *args, **kwargs):
        self.store = {}
    def ping(self):
        return True
    def hgetall(self, key):
        if key == "merchant:MER-1005":
            return {"name": "MER-1005 Test Shop", "status": "ACTIVE"}
        return self.store.get(key, {})
    def hset(self, key, mapping=None, key_id=None, value=None):
        if mapping:
            self.store[key] = mapping
        elif key_id and value:
            if key not in self.store:
                self.store[key] = {}
            self.store[key][key_id] = value
        return 1
    def hexists(self, key, field):
        return True
    def get(self, key):
        return None
    def setex(self, key, time, value):
        return True
    def ttl(self, key):
        return 60
    def hdel(self, key, field):
        return 1

mock_client = MockRedis()
db.redisdb.client = mock_client
db.redisdb.get_redis = lambda: mock_client

sys.path.insert(0, "d:\\OneDrive\\Desktop\\paydesk-mcp")

from security.auth import current_caller_context, CallerContext
from tools.postgres_tools import (
    get_fee_schedule,
    update_fee_schedule,
    get_monthly_accounting_report,
    create_settlement_payout,
    list_merchant_payouts
)
from tools.mongo_tools import (
    list_merchant_disputes,
    create_customer_profile,
    get_customer_profile,
    get_support_ticket,
    update_ticket_status
)
from tools.summary_tools import (
    check_merchant_health,
    get_payment_method_stats
)

def run_test_suite():
    print("============================================================")
    print("RUNNING PAYDESK MCP EXPANDED TOOLS VERIFICATION")
    print("============================================================")

    # 1. Setup authenticated contexts
    # Admin caller context
    admin_context = CallerContext(
        caller_type="admin",
        user_id="ADM-01",
        role="ADMIN",
        scopes=["ledger:read", "ledger:write", "dispute:read", "dispute:write", "ticket:read", "ticket:write", "customer:read", "customer:write", "txn:read", "txn:write"],
        can_view_all_merchants=True
    )

    # Merchant caller context
    merchant_context = CallerContext(
        caller_type="merchant",
        merchant_id="MER-1005",
        role="MERCHANT",
        scopes=["ledger:read", "ledger:write", "dispute:read", "dispute:write", "ticket:read", "ticket:write", "customer:read", "customer:write", "txn:read", "txn:write"]
    )

    # ------------------------------------------------------------
    # Test Case A: PostgreSQL - Fee Schedule & Payouts
    # ------------------------------------------------------------
    print("\n--- Test Case A: PostgreSQL Fee Schedule and Payouts ---")
    current_caller_context.set(admin_context)
    
    # Update fee schedule (should succeed since approved=True and caller is Admin)
    print("Admin updating fee schedule for MER-1005...")
    update_res = update_fee_schedule(
        merchant_id="MER-1005",
        pricing_tier="PREMIUM",
        fixed_fee=0.15,
        percentage_fee=1.85,
        approved=True
    )
    print("Result:", update_res)

    # Get fee schedule for MER-1005 (should show updated PREMIUM settings)
    current_caller_context.set(merchant_context)
    print("\nMerchant getting its own fee schedule...")
    schedule = get_fee_schedule(merchant_id="MER-1005")
    print("Result:", schedule)

    # Create settlement payout with approved=False (should trigger approval gate)
    print("\nMerchant requesting payout without approval (approved=False)...")
    payout_gate = create_settlement_payout(
        merchant_id="MER-1005",
        amount=500.0,
        currency="INR",
        approved=False
    )
    print("Result:", payout_gate)

    # Create settlement payout with approved=True
    print("\nMerchant requesting payout with approval (approved=True)...")
    payout_res = create_settlement_payout(
        merchant_id="MER-1005",
        amount=500.0,
        currency="INR",
        approved=True
    )
    print("Result:", payout_res)

    # List payouts
    print("\nMerchant listing recent payouts...")
    payouts_list = list_merchant_payouts(merchant_id="MER-1005", limit=3)
    print("Result:", payouts_list)

    # ------------------------------------------------------------
    # Test Case B: MongoDB - Customer Profiles
    # ------------------------------------------------------------
    print("\n--- Test Case B: MongoDB Customer Profiles ---")
    
    # Create customer profile (approved=False)
    print("Merchant creating customer profile without approval (approved=False)...")
    cust_gate = create_customer_profile(
        merchant_id="MER-1005",
        email="test_cust@example.com",
        name="John Doe",
        phone="+919876543210",
        approved=False
    )
    print("Result:", cust_gate)

    # Create customer profile (approved=True)
    print("\nMerchant creating customer profile with approval (approved=True)...")
    cust_res = create_customer_profile(
        merchant_id="MER-1005",
        email="test_cust@example.com",
        name="John Doe",
        phone="+919876543210",
        approved=True
    )
    print("Result:", cust_res)
    customer_id = cust_res["customer"]["customer_id"]

    # Get customer profile
    print("\nMerchant retrieving customer profile...")
    cust_profile = get_customer_profile(customer_id=customer_id)
    print("Result:", cust_profile)

    # ------------------------------------------------------------
    # Test Case C: Cross-Database Scopes (Health Report & Payment Stats)
    # ------------------------------------------------------------
    print("\n--- Test Case C: Cross-Database Health and Payment Stats ---")
    
    # Check merchant health (aggregates Redis, Mongo, Postgres)
    # Mocking Redis merchant hash first to let health tool succeed
    from db.redisdb import get_redis
    redis_client = get_redis()
    redis_client.hset("merchant:MER-1005", mapping={"name": "MER-1005 Test Shop", "status": "ACTIVE"})
    
    print("Merchant running check_merchant_health...")
    health = check_merchant_health(merchant_id="MER-1005")
    print("Result:", health)

    # Get payment method stats
    print("\nMerchant retrieving payment method statistics...")
    stats = get_payment_method_stats(merchant_id="MER-1005")
    print("Result:", stats)

    # ------------------------------------------------------------
    # Test Case D: Security and Scoping Limits
    # ------------------------------------------------------------
    print("\n--- Test Case D: Security Scoping and Access Denial ---")
    
    # Merchant MER-1005 trying to read MER-1018's disputes (should fail)
    print("Merchant MER-1005 trying to read disputes of MER-1018...")
    cross_res = list_merchant_disputes(merchant_id="MER-1018")
    print("Result:", cross_res)

    print("\n============================================================")
    print("VERIFICATION SUITE COMPLETED SUCCESSFULLY")
    print("============================================================")

if __name__ == "__main__":
    run_test_suite()
