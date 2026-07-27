from mcp.server.fastmcp import FastMCP

from db.redisdb import get_redis
from db.postgres import get_connection
from db.mongodb import get_db


def register_resources(mcp: FastMCP):

    @mcp.resource("merchant://summary/{merchant_id}")
    def merchant_summary_resource(merchant_id: str):
        """
        Read-only merchant summary.
        """

        # ---------- Redis ----------
        redis_client = get_redis()

        merchant = redis_client.hgetall(f"merchant:{merchant_id}")

        if not merchant:
            return {"error": "Merchant not found"}

        # ---------- PostgreSQL ----------
        conn = get_connection()

        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                COALESCE(
                    SUM(
                        CASE
                            WHEN direction='credit'
                            THEN amount
                            ELSE -amount
                        END
                    ),
                    0
                )
            FROM ledger
            WHERE merchant_id=%s
            """,
            (merchant_id,),
        )

        balance = cur.fetchone()[0]

        cur.close()
        conn.close()

        # ---------- Mongo ----------
        db = get_db()

        transactions = list(
            db.transactions.find(
                {"merchant_id": merchant_id},
                {"_id": 0}
            ).limit(5)
        )

        return {
            "merchant": merchant,
            "balance": float(balance),
            "recent_transactions": transactions
        }


    @mcp.resource("merchant://policy/refund")
    def refund_policy():
        """
        Refund policy resource.
        """
        return {
            "title": "Refund Policy",
            "rules": [
                "Refund requests must be raised within 7 days.",
                "Only successful transactions are refundable.",
                "Refunds require merchant approval.",
                "Refund status can be tracked using transaction ID."
            ]
        }

    @mcp.resource("merchant://profile/{merchant_id}")
    def merchant_profile_resource(merchant_id: str):
        """
        Read merchant profile details from Redis.
        """
        redis_client = get_redis()
        profile = redis_client.hgetall(f"merchant:{merchant_id}")
        return profile if profile else {"error": "Profile not found"}

    @mcp.resource("merchant://balance/{merchant_id}")
    def merchant_balance_resource(merchant_id: str):
        """
        Read current ledger balance details from PostgreSQL.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 
                        SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE -amount END)
                    FROM ledger
                    WHERE account = 'merchant_payable' AND merchant_id = %s
                    """,
                    (merchant_id,)
                )
                res = cur.fetchone()[0]
                balance = float(res) if res is not None else 0.0
        finally:
            conn.close()
        return {"merchant_id": merchant_id, "payable_balance": balance}

    @mcp.resource("merchant://transactions/recent/{merchant_id}")
    def merchant_recent_transactions_resource(merchant_id: str):
        """
        Read the latest 10 transactions from MongoDB.
        """
        db = get_db()
        txns = list(db.transactions.find({"merchant_id": merchant_id}, {"_id": 0}).sort("created_at", -1).limit(10))
        return {"merchant_id": merchant_id, "recent_transactions": txns}

    @mcp.resource("merchant://disputes/active/{merchant_id}")
    def merchant_active_disputes_resource(merchant_id: str):
        """
        Read active disputes from MongoDB.
        """
        db = get_db()
        disputes = list(db.disputes.find({"merchant_id": merchant_id, "status": {"$in": ["OPEN", "UNDER_REVIEW"]}}, {"_id": 0}))
        return {"merchant_id": merchant_id, "active_disputes": disputes}

    @mcp.resource("merchant://tickets/open/{merchant_id}")
    def merchant_open_tickets_resource(merchant_id: str):
        """
        Read open support tickets from MongoDB.
        """
        db = get_db()
        tickets = list(db.tickets.find({"merchant_id": merchant_id, "status": {"$in": ["OPEN", "IN_PROGRESS"]}}, {"_id": 0}))
        return {"merchant_id": merchant_id, "open_tickets": tickets}

    @mcp.resource("merchant://payouts/history/{merchant_id}")
    def merchant_payout_history_resource(merchant_id: str):
        """
        Read payout history from PostgreSQL ledger.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT settlement_id, SUM(amount), currency, MIN(posted_at)
                    FROM ledger
                    WHERE merchant_id = %s
                      AND account = 'merchant_payable'
                      AND direction = 'DEBIT'
                      AND settlement_id IS NOT NULL AND settlement_id != ''
                    GROUP BY settlement_id, currency
                    ORDER BY MIN(posted_at) DESC;
                    """,
                    (merchant_id,)
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        
        payouts = [{"payout_id": r[0], "amount": float(r[1]), "currency": r[2], "created_at": r[3].isoformat()} for r in rows]
        return {"merchant_id": merchant_id, "payouts": payouts}

    @mcp.resource("merchant://api-keys/metadata/{merchant_id}")
    def merchant_api_keys_resource(merchant_id: str):
        """
        Read active API key metadata from Redis.
        """
        import json
        redis_client = get_redis()
        keys_data = redis_client.hgetall(f"merchant:{merchant_id}:api_keys")
        keys = []
        for kid, val_str in keys_data.items():
            try:
                keys.append(json.loads(val_str))
            except Exception:
                keys.append({"key_id": kid, "raw": val_str})
        return {"merchant_id": merchant_id, "api_keys": keys}

    @mcp.resource("merchant://webhooks/config/{merchant_id}")
    def merchant_webhooks_resource(merchant_id: str):
        """
        Read merchant webhook configuration from Redis.
        """
        import json
        redis_client = get_redis()
        config = redis_client.hgetall(f"merchant:{merchant_id}:webhook")
        if config and "events" in config:
            try:
                config["events"] = json.loads(config["events"])
            except Exception:
                pass
        return {"merchant_id": merchant_id, "webhook_config": config}

    @mcp.resource("merchant://fees/schedule/{merchant_id}")
    def merchant_fees_resource(merchant_id: str):
        """
        Read fee schedule pricing tier from PostgreSQL.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Ensure table exists
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS fee_schedule (
                        merchant_id VARCHAR(50) PRIMARY KEY,
                        pricing_tier VARCHAR(50) DEFAULT 'STANDARD',
                        fixed_fee DECIMAL(10, 2) DEFAULT 0.30,
                        percentage_fee DECIMAL(5, 2) DEFAULT 2.90,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.commit()
                cur.execute("SELECT pricing_tier, fixed_fee, percentage_fee FROM fee_schedule WHERE merchant_id = %s", (merchant_id,))
                row = cur.fetchone()
                if not row:
                    cur.execute("INSERT INTO fee_schedule (merchant_id) VALUES (%s) ON CONFLICT DO NOTHING", (merchant_id,))
                    conn.commit()
                    row = ("STANDARD", 0.30, 2.90)
        finally:
            conn.close()
        return {
            "merchant_id": merchant_id,
            "pricing_tier": row[0],
            "fixed_fee": float(row[1]),
            "percentage_fee": float(row[2])
        }

    @mcp.resource("merchant://audit-logs/{merchant_id}")
    def merchant_audit_logs_resource(merchant_id: str):
        """
        Read merchant access audit logs from MongoDB.
        """
        db = get_db()
        logs = list(db.audit_logs.find({"caller_id": merchant_id}, {"_id": 0}).sort("timestamp", -1).limit(20))
        return {"merchant_id": merchant_id, "audit_logs": logs}

    @mcp.resource("system://policies/refund-rules")
    def system_refund_rules_resource():
        """
        Get global system policies for payment refunds.
        """
        return (
            "# PayDesk Refund Guidelines\n\n"
            "1. Refund limits: Up to 100% of the original transaction amount.\n"
            "2. Time frames: Must be requested within 180 days of transaction settlement.\n"
            "3. Gateway Fees: Original processing fees are non-refundable.\n"
            "4. Approvals: High-value refunds (> $5,000) require manual risk team review.\n"
        )

    @mcp.resource("system://policies/dispute-handling")
    def system_dispute_policy_resource():
        """
        Get dispute resolution policies and regulations.
        """
        return (
            "# Dispute and Chargeback Policies\n\n"
            "1. Evidence submission timeline: Evidence must be uploaded within 15 days of dispute creation.\n"
            "2. Dispute fees: A standard chargeback fee of $15.00 is debited from the merchant account.\n"
            "3. Resolution: Card network decisions are final and take up to 90 days.\n"
            "4. Ledger impact: Funds are put on hold immediately during dispute review.\n"
        )

    @mcp.resource("system://policies/compliance-requirements")
    def system_compliance_policy_resource():
        """
        Get anti-money laundering and payout compliance requirements.
        """
        return (
            "# Compliance & Payout Regulations\n\n"
            "1. Daily payout limits: Standard merchants: $50,000. Premium merchants: $250,000.\n"
            "2. Verification: KYC/KYB documents must be approved before the first payout.\n"
            "3. Suspicious Activity: Transactions showing structural velocity spikes trigger immediate ledger holds.\n"
        )

    @mcp.resource("merchant://health/score/{merchant_id}")
    def merchant_health_score_resource(merchant_id: str):
        """
        Get merchant risk and operations health metrics.
        """
        db = get_db()
        open_disputes = db.disputes.count_documents({"merchant_id": merchant_id, "status": {"$in": ["OPEN", "UNDER_REVIEW"]}})
        open_tickets = db.tickets.count_documents({"merchant_id": merchant_id, "status": {"$in": ["OPEN", "IN_PROGRESS"]}})
        
        score = 100.0 - (open_disputes * 15.0) - (open_tickets * 3.0)
        score = max(0.0, score)
        return {
            "merchant_id": merchant_id,
            "operational_health_score": score,
            "active_disputes_count": open_disputes,
            "open_tickets_count": open_tickets,
            "risk_classification": "LOW_RISK" if score >= 85 else "MEDIUM_RISK" if score >= 60 else "HIGH_RISK"
        }

    @mcp.resource("customer://profile/{customer_id}")
    def customer_profile_resource(customer_id: str):
        """
        Read customer profile data from MongoDB.
        """
        db = get_db()
        customer = db.customers.find_one({"customer_id": customer_id}, {"_id": 0})
        return customer if customer else {"error": f"Customer '{customer_id}' not found"}