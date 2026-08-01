import sys
import datetime
import uuid
import pytest
from unittest.mock import MagicMock

# Mock Redis client BEFORE importing tools
import db.redisdb

class MockRedis:
    def __init__(self, *args, **kwargs):
        self.store = {}
    def ping(self):
        return True
    def exists(self, key):
        if key == "merchant:MER-9999":
            return False
        # Return True for merchant keys to simulate they exist
        if key.startswith("merchant:"):
            return True
        return key in self.store
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

@pytest.fixture
def admin_context():
    return CallerContext(
        caller_type="admin",
        user_id="ADM-01",
        role="ADMIN",
        scopes=["ledger:read", "ledger:write", "dispute:read", "dispute:write", "ticket:read", "ticket:write", "customer:read", "customer:write", "txn:read", "txn:write"],
        can_view_all_merchants=True
    )

@pytest.fixture
def merchant_context():
    return CallerContext(
        caller_type="merchant",
        merchant_id="MER-1005",
        role="MERCHANT",
        scopes=["ledger:read", "ledger:write", "dispute:read", "dispute:write", "ticket:read", "ticket:write", "customer:read", "customer:write", "txn:read", "txn:write"]
    )

# Test Case A: PostgreSQL - Fee Schedule & Payouts
def test_postgres_fee_schedule_and_payouts(admin_context, merchant_context):
    current_caller_context.set(admin_context)
    
    # Update fee schedule
    update_res = update_fee_schedule(
        merchant_id="MER-1005",
        pricing_tier="PREMIUM",
        fixed_fee=0.15,
        percentage_fee=1.85,
        approved=True
    )
    assert update_res.get("message") == "Fee schedule updated successfully."

    # Get fee schedule
    current_caller_context.set(merchant_context)
    schedule = get_fee_schedule(merchant_id="MER-1005")
    assert schedule.get("pricing_tier") == "PREMIUM"
    assert schedule.get("fixed_fee") == 0.15
    assert schedule.get("percentage_fee") == 1.85

    # Create settlement payout with approved=False
    payout_gate = create_settlement_payout(
        merchant_id="MER-1005",
        amount=500.0,
        currency="INR",
        request_id=f"REQ-STL-{uuid.uuid4().hex[:6]}",
        approved=False
    )
    assert payout_gate.get("status") == "AWAITING_APPROVAL"

    # Create settlement payout with approved=True
    payout_res = create_settlement_payout(
        merchant_id="MER-1005",
        amount=500.0,
        currency="INR",
        request_id=f"REQ-STL-{uuid.uuid4().hex[:6]}",
        approved=True
    )
    assert payout_res.get("message") == "Payout initiated successfully."

    # List payouts
    payouts_list = list_merchant_payouts(merchant_id="MER-1005", limit=3)
    assert "payouts" in payouts_list
    assert isinstance(payouts_list["payouts"], list)

# Test Case B: MongoDB - Customer Profiles
def test_mongo_customer_profiles(merchant_context):
    current_caller_context.set(merchant_context)
    
    # Create customer profile (approved=False)
    cust_gate = create_customer_profile(
        merchant_id="MER-1005",
        email="test_cust@example.com",
        name="John Doe",
        phone="+919876543210",
        approved=False
    )
    assert cust_gate.get("status") == "AWAITING_APPROVAL"

    # Create customer profile (approved=True)
    cust_res = create_customer_profile(
        merchant_id="MER-1005",
        email="test_cust@example.com",
        name="John Doe",
        phone="+919876543210",
        approved=True
    )
    assert cust_res.get("message") == "Customer profile created successfully."
    customer_id = cust_res["customer"]["customer_id"]

    # Get customer profile
    cust_profile = get_customer_profile(customer_id=customer_id)
    assert cust_profile.get("customer_id") == customer_id
    assert cust_profile.get("name") == "John Doe"

# Test Case C: Cross-Database Scopes (Health Report & Payment Stats)
def test_cross_database_health_and_stats(merchant_context):
    current_caller_context.set(merchant_context)
    
    # Setup mock redis values
    from db.redisdb import get_redis
    redis_client = get_redis()
    redis_client.hset("merchant:MER-1005", mapping={"name": "MER-1005 Test Shop", "status": "ACTIVE"})
    
    # Check merchant health
    health = check_merchant_health(merchant_id="MER-1005")
    assert health.get("merchant_id") == "MER-1005"
    assert "risk_health_score" in health

    # Get payment method stats
    stats = get_payment_method_stats(merchant_id="MER-1005")
    assert stats.get("merchant_id") == "MER-1005"
    assert "payment_method_stats" in stats

# Test Case D: Security and Scoping Limits
def test_security_scoping_limits(merchant_context):
    current_caller_context.set(merchant_context)
    
    # Merchant MER-1005 trying to read MER-1018's disputes (scoping overrides it to MER-1005)
    cross_res = list_merchant_disputes(merchant_id="MER-1018")
    assert cross_res.get("merchant_id") == "MER-1005"

if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
