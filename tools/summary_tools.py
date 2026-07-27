from mcp_app import mcp
from db.redisdb import get_redis
from db.mongodb import get_db
from db.postgres import get_connection
from security.auth import get_current_caller
from security.scope import is_authorized
from security.audit import audit_logged
import uuid
import datetime
import json
from typing import Optional

redis_client = get_redis()
mongo_db = get_db()


@mcp.tool
@audit_logged
def get_merchant_summary(merchant_id: str):
    """
    Get a complete merchant summary by combining data from:
    - Redis (merchant profile)
    - PostgreSQL (merchant balance)
    - MongoDB (recent transactions)
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization (needs both txn:read and ledger:read scopes for admins)
    if not is_authorized(context, merchant_id, required_scopes=["txn:read", "ledger:read"]):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access summary data for merchant '{merchant_id}'."}

    # -----------------------------
    # Get Merchant Profile (Redis)
    # -----------------------------
    merchant = redis_client.hgetall(f"merchant:{merchant_id}")

    if not merchant:
        return {
            "error": f"Merchant '{merchant_id}' not found."
        }

    # -----------------------------
    # Get Merchant Balance (PostgreSQL)
    # -----------------------------
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM ledger
                WHERE account = 'merchant_payable'
                AND merchant_id = %s
                """,
                (merchant_id,)
            )

            balance = cur.fetchone()[0]

    finally:
        conn.close()

    # -----------------------------
    # Get Recent Transactions (MongoDB)
    # -----------------------------
    transactions = list(
        mongo_db.transactions.find(
            {"merchant_id": merchant_id},
            {"_id": 0}
        )
        .sort("created_at", -1)
        .limit(5)
    )

    # -----------------------------
    # Return Combined Response
    # -----------------------------
    return {
        "merchant": merchant,
        "balance": float(balance),
        "recent_transactions": transactions
    }


@mcp.tool
@audit_logged
def process_refund(
    txn_id: str,
    amount: float,
    reason: str,
    approved: bool = False
):
    """
    Process a refund for a transaction. Debits merchant ledger balance and updates transaction status in Mongo.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # 1. Fetch transaction from MongoDB
    txn = mongo_db.transactions.find_one({"txn_id": txn_id}, {"_id": 0})
    if not txn:
        return {"error": f"Transaction '{txn_id}' not found."}

    merchant_id = txn["merchant_id"]
    if not is_authorized(context, merchant_id, required_scopes=["txn:write", "ledger:write"]):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot process refunds for merchant '{merchant_id}'."}

    if txn["status"] not in ["CAPTURED", "SETTLED", "PARTIALLY_REFUNDED"]:
        return {"error": f"Validation failed: Transaction with status '{txn['status']}' cannot be refunded."}

    original_amount = float(txn["amount"])
    if amount <= 0 or amount > original_amount:
        return {"error": f"Validation failed: Invalid refund amount. Original: {original_amount}, Requested: {amount}"}

    # 2. Check current refunds in PostgreSQL ledger to avoid over-refunding
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM ledger
                WHERE txn_id = %s
                  AND account = 'merchant_payable'
                  AND direction = 'DEBIT'
                  AND settlement_id IS NULL;
                """,
                (txn_id,)
            )
            already_refunded = float(cur.fetchone()[0])
            
            # Fetch merchant payable balance
            cur.execute(
                """
                SELECT SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE -amount END)
                FROM ledger
                WHERE account = 'merchant_payable'
                  AND merchant_id = %s;
                """,
                (merchant_id,)
            )
            balance_row = cur.fetchone()
            current_balance = float(balance_row[0]) if balance_row and balance_row[0] is not None else 0.0

        if already_refunded + amount > original_amount:
            conn.close()
            return {"error": f"Validation failed: Total refunded ({already_refunded + amount}) exceeds transaction amount ({original_amount})."}

        if current_balance < amount:
            conn.close()
            return {"error": f"Validation failed: Insufficient merchant balance ({current_balance}) to process refund ({amount})."}

        if not approved:
            conn.close()
            return {
                "status": "AWAITING_APPROVAL",
                "message": "Refund processing requires explicit approval. Set approved=True to submit.",
                "details": {
                    "txn_id": txn_id,
                    "amount": amount,
                    "reason": reason,
                    "merchant_id": merchant_id,
                    "available_balance": current_balance
                }
            }

        # 3. Insert refund ledger entries in Postgres
        entry_id_debit = f"LED-REF-{uuid.uuid4().hex[:6].upper()}"
        entry_id_credit = f"LED-REF-{uuid.uuid4().hex[:6].upper()}"
        posted_at = datetime.datetime.now()

        with conn.cursor() as cur:
            # Debit merchant_payable
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, NULL, 'merchant_payable', 'DEBIT', %s, %s, %s)
                """,
                (entry_id_debit, txn_id, merchant_id, amount, txn["currency"], posted_at)
            )
            # Credit customer_clearing
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, NULL, 'customer_clearing', 'CREDIT', %s, %s, %s)
                """,
                (entry_id_credit, txn_id, merchant_id, amount, txn["currency"], posted_at)
            )
            conn.commit()
    finally:
        conn.close()

    # 4. Update transaction status in MongoDB
    new_status = "REFUNDED" if already_refunded + amount == original_amount else "PARTIALLY_REFUNDED"
    mongo_db.transactions.update_one(
        {"txn_id": txn_id},
        {"$set": {"status": new_status, "refunded_amount": already_refunded + amount}}
    )

    return {
        "message": "Refund processed successfully.",
        "txn_id": txn_id,
        "refund_amount": amount,
        "status": new_status
    }


@mcp.tool
@audit_logged
def get_chargeback_summary(merchant_id: str):
    """
    Correlates dispute records from MongoDB with PostgreSQL ledger entries to show chargeback statistics.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scopes=["dispute:read", "ledger:read"]):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access chargeback summary for merchant '{merchant_id}'."}

    # Fetch disputes from MongoDB
    disputes = list(mongo_db.disputes.find({"merchant_id": merchant_id}, {"_id": 0}))
    dispute_ids = [d["dispute_id"] for d in disputes]

    # Query PostgreSQL ledger for chargeback-related debits
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT txn_id, SUM(amount), currency
                FROM ledger
                WHERE merchant_id = %s
                  AND account = 'merchant_payable'
                  AND direction = 'DEBIT'
                  AND (txn_id LIKE 'TXN%' OR txn_id LIKE 'DISP%')
                  AND (settlement_id IS NULL OR settlement_id = '')
                GROUP BY txn_id, currency;
                """,
                (merchant_id,)
            )
            ledger_rows = cur.fetchall()
    finally:
        conn.close()

    ledger_debits = {r[0]: {"amount": float(r[1]), "currency": r[2]} for r in ledger_rows}

    total_contested_amount = 0.0
    active_disputes_count = 0
    financial_impact = 0.0

    dispute_list_summary = []
    for d in disputes:
        tid = d.get("txn_id")
        status = d.get("status")
        amt = float(d.get("amount", 0.0))

        total_contested_amount += amt
        if status in ["OPEN", "UNDER_REVIEW"]:
            active_disputes_count += 1

        # Correlate with ledger debit
        impact = ledger_debits.get(tid, {}).get("amount", 0.0)
        financial_impact += impact

        dispute_list_summary.append({
            "dispute_id": d.get("dispute_id"),
            "txn_id": tid,
            "status": status,
            "amount": amt,
            "ledger_impact": impact,
            "currency": d.get("currency", "INR")
        })

    return {
        "merchant_id": merchant_id,
        "total_disputes": len(disputes),
        "active_disputes": active_disputes_count,
        "total_contested_amount": round(total_contested_amount, 2),
        "ledger_debit_impact": round(financial_impact, 2),
        "disputes": dispute_list_summary
    }


@mcp.tool
@audit_logged
def create_transaction(
    merchant_id: str,
    amount: float,
    currency: str,
    payment_method: str,
    customer_id: Optional[str] = None,
    approved: bool = False
):
    """
    Simulate creating a new payment transaction. Checks Redis, inserts in Mongo, and creates ledger entries in Postgres.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scopes=["txn:write", "ledger:write"]):
        return {"error": f"Unauthorized: Caller is not authorized to create transactions for merchant '{merchant_id}'."}

    # 1. Validate Merchant Profile in Redis
    merchant_profile = redis_client.hgetall(f"merchant:{merchant_id}")
    if not merchant_profile:
        return {"error": f"Validation failed: Merchant '{merchant_id}' does not exist or is inactive in Redis."}

    if amount <= 0:
        return {"error": "Validation failed: Transaction amount must be positive."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Transaction creation requires explicit approval. Set approved=True to submit.",
            "details": {
                "merchant_id": merchant_id,
                "amount": amount,
                "currency": currency,
                "payment_method": payment_method
            }
        }

    # 2. Fetch Fee Schedule from Postgres to compute transaction fees
    conn = get_connection()
    fixed_fee = 0.30
    pct_fee = 2.90
    try:
        with conn.cursor() as cur:
            # check fee schedule table
            cur.execute("SELECT fixed_fee, percentage_fee FROM fee_schedule WHERE merchant_id = %s", (merchant_id,))
            row = cur.fetchone()
            if row:
                fixed_fee = float(row[0])
                pct_fee = float(row[1])
    except Exception:
        pass
    finally:
        conn.close()

    # Compute Fee
    fee = round((amount * (pct_fee / 100.0)) + fixed_fee, 2)
    net_amount = round(amount - fee, 2)

    txn_id = f"TXN-{uuid.uuid4().hex[:6].upper()}"
    created_at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # 3. Create Transaction Document in MongoDB
    new_txn = {
        "txn_id": txn_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id or "",
        "amount": amount,
        "currency": currency,
        "status": "SETTLED",
        "payment_method": payment_method,
        "fee": fee,
        "created_at": created_at
    }
    mongo_db.transactions.insert_one(dict(new_txn))
    new_txn.pop("_id", None)

    # 4. Create Ledger Entries in PostgreSQL
    conn = get_connection()
    try:
        entry_id_clear = f"LED-{uuid.uuid4().hex[:6].upper()}"
        entry_id_payable = f"LED-{uuid.uuid4().hex[:6].upper()}"
        entry_id_fee = f"LED-{uuid.uuid4().hex[:6].upper()}"
        posted_at = datetime.datetime.now()

        with conn.cursor() as cur:
            # Debit customer_clearing
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, NULL, 'customer_clearing', 'DEBIT', %s, %s, %s)
                """,
                (entry_id_clear, txn_id, merchant_id, amount, currency, posted_at)
            )
            # Credit merchant_payable
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, NULL, 'merchant_payable', 'CREDIT', %s, %s, %s)
                """,
                (entry_id_payable, txn_id, merchant_id, net_amount, currency, posted_at)
            )
            # Credit fee_income
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, NULL, 'fee_income', 'CREDIT', %s, %s, %s)
                """,
                (entry_id_fee, txn_id, merchant_id, fee, currency, posted_at)
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "message": "Transaction processed successfully.",
        "transaction": new_txn,
        "fee_calculation": {
            "percentage_rate": f"{pct_fee}%",
            "fixed_rate": fixed_fee,
            "calculated_fee": fee,
            "net_amount": net_amount
        }
    }


@mcp.tool
@audit_logged
def capture_payment(txn_id: str, approved: bool = False):
    """
    Capture a pre-authorized payment. Updates status in Mongo and ledger entries in Postgres.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    txn = mongo_db.transactions.find_one({"txn_id": txn_id}, {"_id": 0})
    if not txn:
        return {"error": f"Transaction '{txn_id}' not found."}

    merchant_id = txn["merchant_id"]
    if not is_authorized(context, merchant_id, required_scopes=["txn:write", "ledger:write"]):
        return {"error": f"Unauthorized: Caller is not authorized to capture payments for merchant '{merchant_id}'."}

    if txn["status"] != "AUTHORIZED":
        return {"error": f"Validation failed: Only transactions with AUTHORIZED status can be captured. Current status: '{txn['status']}'."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Capture payment requires explicit approval. Set approved=True to capture.",
            "details": {"txn_id": txn_id, "amount": txn["amount"], "merchant_id": merchant_id}
        }

    # Fetch fee rate
    conn = get_connection()
    fixed_fee = 0.30
    pct_fee = 2.90
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fixed_fee, percentage_fee FROM fee_schedule WHERE merchant_id = %s", (merchant_id,))
            row = cur.fetchone()
            if row:
                fixed_fee = float(row[0])
                pct_fee = float(row[1])
    except Exception:
        pass
    finally:
        conn.close()

    amount = float(txn["amount"])
    fee = round((amount * (pct_fee / 100.0)) + fixed_fee, 2)
    net_amount = round(amount - fee, 2)

    # 1. Update Mongo transaction status
    mongo_db.transactions.update_one({"txn_id": txn_id}, {"$set": {"status": "CAPTURED", "fee": fee}})

    # 2. Write to Postgres ledger
    conn = get_connection()
    try:
        entry_id_clear = f"LED-{uuid.uuid4().hex[:6].upper()}"
        entry_id_payable = f"LED-{uuid.uuid4().hex[:6].upper()}"
        entry_id_fee = f"LED-{uuid.uuid4().hex[:6].upper()}"
        posted_at = datetime.datetime.now()

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, NULL, 'customer_clearing', 'DEBIT', %s, %s, %s)
                """,
                (entry_id_clear, txn_id, merchant_id, amount, txn["currency"], posted_at)
            )
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, NULL, 'merchant_payable', 'CREDIT', %s, %s, %s)
                """,
                (entry_id_payable, txn_id, merchant_id, net_amount, txn["currency"], posted_at)
            )
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, NULL, 'fee_income', 'CREDIT', %s, %s, %s)
                """,
                (entry_id_fee, txn_id, merchant_id, fee, txn["currency"], posted_at)
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "message": "Payment captured successfully.",
        "txn_id": txn_id,
        "amount": amount,
        "status": "CAPTURED"
    }


@mcp.tool
@audit_logged
def get_payment_method_stats(merchant_id: str):
    """
    Aggregate transaction success count from Mongo and fee metrics from Postgres by payment method.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scopes=["txn:read", "ledger:read"]):
        return {"error": f"Unauthorized: Caller is not authorized to read reports for merchant '{merchant_id}'."}

    # Query Mongo for transactions grouped by payment method
    pipeline = [
        {"$match": {"merchant_id": merchant_id}},
        {
            "$group": {
                "_id": "$payment_method",
                "total_count": {"$sum": 1},
                "successful_count": {
                    "$sum": {"$cond": [{"$in": ["$status", ["SETTLED", "CAPTURED", "REFUNDED"]]}, 1, 0]}
                },
                "total_volume": {"$sum": "$amount"}
            }
        }
    ]
    mongo_stats = list(mongo_db.transactions.aggregate(pipeline))

    # Query Postgres for average fee rates
    conn = get_connection()
    fees_by_txn = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT txn_id, 
                       SUM(CASE WHEN account = 'fee_income' THEN amount ELSE 0 END) AS fee_amount
                FROM ledger
                WHERE merchant_id = %s
                GROUP BY txn_id;
                """,
                (merchant_id,)
            )
            fees_by_txn = {r[0]: float(r[1]) for r in cur.fetchall()}
    finally:
        conn.close()

    # Correlate payment methods in Mongo transactions to fee data
    results = []
    for stat in mongo_stats:
        method = stat["_id"] or "UNKNOWN"
        total_count = stat["total_count"]
        successful_count = stat["successful_count"]
        volume = float(stat["total_volume"])

        # Fetch all transactions matching this method to calculate average fees
        txns_with_method = list(mongo_db.transactions.find({"merchant_id": merchant_id, "payment_method": method}, {"txn_id": 1}))
        total_fees = 0.0
        for t in txns_with_method:
            total_fees += fees_by_txn.get(t["txn_id"], 0.0)

        results.append({
            "payment_method": method,
            "total_transactions": total_count,
            "successful_transactions": successful_count,
            "success_rate": round((successful_count / total_count) * 100, 2) if total_count > 0 else 0.0,
            "total_volume": round(volume, 2),
            "total_fees_incurred": round(total_fees, 2)
        })

    return {"merchant_id": merchant_id, "payment_method_stats": results}


@mcp.tool
@audit_logged
def check_merchant_health(merchant_id: str):
    """
    Risk report combining Redis profile, Postgres balance, and MongoDB disputes/tickets.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scopes=["txn:read", "ledger:read", "ticket:read", "dispute:read"]):
        return {"error": f"Unauthorized: Caller is not authorized to audit merchant health."}

    # 1. Redis profile
    merchant_profile = redis_client.hgetall(f"merchant:{merchant_id}")
    if not merchant_profile:
        return {"error": f"Merchant '{merchant_id}' not found."}

    # 2. Postgres balance
    conn = get_connection()
    balance = 0.0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE -amount END)
                FROM ledger
                WHERE account = 'merchant_payable' AND merchant_id = %s
                """,
                (merchant_id,)
            )
            res = cur.fetchone()[0]
            balance = float(res) if res is not None else 0.0
    finally:
        conn.close()

    # 3. Mongo disputes
    open_disputes = mongo_db.disputes.count_documents({"merchant_id": merchant_id, "status": {"$in": ["OPEN", "UNDER_REVIEW"]}})
    
    # 4. Mongo support tickets
    open_tickets = mongo_db.tickets.count_documents({"merchant_id": merchant_id, "status": {"$in": ["OPEN", "IN_PROGRESS"]}})

    # Calculate health score
    health_score = 100.0
    reasons = []

    if open_disputes > 0:
        deduction = open_disputes * 15.0
        health_score -= deduction
        reasons.append(f"Deducted {deduction} pts for {open_disputes} active dispute(s).")

    if open_tickets > 0:
        deduction = open_tickets * 3.0
        health_score -= deduction
        reasons.append(f"Deducted {deduction} pts for {open_tickets} open support ticket(s).")

    if balance < 0:
        health_score -= 25.0
        reasons.append("Deducted 25 pts for negative ledger account balance.")

    health_score = max(0.0, health_score)

    status = "EXCELLENT" if health_score >= 90 else "GOOD" if health_score >= 70 else "NEEDS_ATTENTION" if health_score >= 50 else "HIGH_RISK"

    return {
        "merchant_id": merchant_id,
        "merchant_name": merchant_profile.get("name", "Unknown"),
        "ledger_balance": balance,
        "active_disputes": open_disputes,
        "open_tickets": open_tickets,
        "risk_health_score": health_score,
        "risk_status": status,
        "findings": reasons if reasons else ["No issues found. Merchant profile is healthy."]
    }


@mcp.tool
@audit_logged
def get_webhook_endpoint_health(merchant_id: str):
    """
    Get webhook configuration status (Redis) and check delivery failure rate (Mongo).
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scopes=["webhook:read"]):
        return {"error": f"Unauthorized: Caller is not authorized to read webhook health."}

    # 1. Fetch URL from Redis
    config = redis_client.hgetall(f"merchant:{merchant_id}:webhook")
    if not config:
        return {"merchant_id": merchant_id, "status": "NOT_CONFIGURED", "message": "No webhook endpoint configured."}

    url = config.get("url")

    # 2. Fetch delivery logs in MongoDB
    total_logs = mongo_db.webhook_logs.count_documents({"merchant_id": merchant_id})
    failed_logs = mongo_db.webhook_logs.count_documents({"merchant_id": merchant_id, "status": "FAILED"})

    failure_rate = (failed_logs / total_logs) * 100 if total_logs > 0 else 0.0

    status = "HEALTHY" if failure_rate < 10 else "DEGRADED" if failure_rate < 30 else "CRITICAL"

    return {
        "merchant_id": merchant_id,
        "webhook_url": url,
        "total_deliveries_logged": total_logs,
        "failed_deliveries": failed_logs,
        "failure_rate_percentage": round(failure_rate, 2),
        "health_status": status
    }


@mcp.tool
@audit_logged
def resolve_dispute_chargeback(
    dispute_id: str,
    resolution: str,
    approved: bool = False
):
    """
    Resolve dispute document in MongoDB and adjust PostgreSQL ledger holds.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    dispute = mongo_db.disputes.find_one({"dispute_id": dispute_id}, {"_id": 0})
    if not dispute:
        return {"error": f"Dispute '{dispute_id}' not found."}

    merchant_id = dispute["merchant_id"]
    if not is_authorized(context, merchant_id, required_scopes=["dispute:write", "ledger:write"]):
        return {"error": f"Unauthorized: Caller is not authorized to resolve dispute '{dispute_id}'."}

    if dispute.get("status") not in ["OPEN", "UNDER_REVIEW"]:
        return {"error": f"Validation failed: Dispute has status '{dispute.get('status')}' and is already resolved."}

    valid_resolutions = ["WON_MERCHANT", "LOST_MERCHANT"]
    if resolution not in valid_resolutions:
        return {"error": f"Validation failed: Resolution must be one of {valid_resolutions}."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Resolving a dispute requires explicit approval. Set approved=True to submit.",
            "details": {
                "dispute_id": dispute_id,
                "txn_id": dispute["txn_id"],
                "resolution": resolution,
                "amount": dispute["amount"]
            }
        }

    # If WON_MERCHANT, credit the merchant_payable back in PostgreSQL ledger
    chargeback_reversed = False
    if resolution == "WON_MERCHANT":
        amount = float(dispute["amount"])
        currency = dispute["currency"]
        
        conn = get_connection()
        try:
            entry_id = f"LED-DISP-REV-{uuid.uuid4().hex[:6].upper()}"
            posted_at = datetime.datetime.now()
            with conn.cursor() as cur:
                # Credit merchant_payable back
                cur.execute(
                    """
                    INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                    VALUES (%s, %s, %s, '', 'merchant_payable', 'CREDIT', %s, %s, %s)
                    """,
                    (entry_id, dispute["txn_id"], merchant_id, amount, currency, posted_at)
                )
                conn.commit()
                chargeback_reversed = True
        finally:
            conn.close()

    # Update dispute status in MongoDB
    mongo_db.disputes.update_one(
        {"dispute_id": dispute_id},
        {"$set": {"status": "RESOLVED_MERCHANT" if resolution == "WON_MERCHANT" else "RESOLVED_CUSTOMER"}}
    )

    return {
        "message": f"Dispute resolution set to '{resolution}' successfully.",
        "dispute_id": dispute_id,
        "chargeback_hold_released": chargeback_reversed,
        "resolution": resolution
    }


@mcp.tool
@audit_logged
def get_customer_lifetime_value(customer_id: str):
    """
    Calculate customer purchase volumes, fee summaries, and metrics across Mongo and Postgres.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Fetch customer from Mongo
    customer = mongo_db.customers.find_one({"customer_id": customer_id}, {"_id": 0})
    if not customer:
        return {"error": f"Customer '{customer_id}' not found."}

    merchant_id = customer["merchant_id"]
    if not is_authorized(context, merchant_id, required_scopes=["customer:read", "ledger:read"]):
        return {"error": f"Unauthorized: Caller cannot view stats for this customer."}

    # Fetch customer transactions in Mongo
    txns = list(mongo_db.transactions.find({"customer_id": customer_id}, {"_id": 0}))
    txn_ids = [t["txn_id"] for t in txns]

    # Query Postgres for actual sums
    conn = get_connection()
    total_spent = 0.0
    total_fees = 0.0
    try:
        with conn.cursor() as cur:
            if txn_ids:
                cur.execute(
                    """
                    SELECT 
                        SUM(CASE WHEN account = 'merchant_payable' AND direction = 'CREDIT' THEN amount ELSE 0 END) AS net_spent,
                        SUM(CASE WHEN account = 'fee_income' THEN amount ELSE 0 END) AS fees
                    FROM ledger
                    WHERE txn_id IN %s;
                    """,
                    (tuple(txn_ids),)
                )
                res = cur.fetchone()
                if res:
                    total_spent = float(res[0]) if res[0] is not None else 0.0
                    total_fees = float(res[1]) if res[1] is not None else 0.0
    finally:
        conn.close()

    total_gross = total_spent + total_fees

    return {
        "customer_id": customer_id,
        "customer_name": customer.get("name"),
        "customer_email": customer.get("email"),
        "total_transactions": len(txns),
        "successful_transactions": sum(1 for t in txns if t["status"] in ["SETTLED", "CAPTURED"]),
        "lifetime_gross_spent": round(total_gross, 2),
        "lifetime_net_merchant_volume": round(total_spent, 2),
        "total_gateway_fees": round(total_fees, 2)
    }


@mcp.tool
@audit_logged
def admin_audit_merchant(merchant_id: str):
    """
    Compliance audit tool correlating audit logs (Mongo), keys (Redis), and ledger stats (Postgres). Only accessible by admins.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type != "admin" or context.role != "ADMIN":
        return {"error": "Unauthorized: Only administrators can execute compliance audits."}

    # 1. Get profile from Redis
    merchant_profile = redis_client.hgetall(f"merchant:{merchant_id}")
    if not merchant_profile:
        return {"error": f"Merchant '{merchant_id}' not found."}

    # 2. Get API keys from Redis
    keys_data = redis_client.hgetall(f"merchant:{merchant_id}:api_keys")
    active_keys_count = len(keys_data)

    # 3. Get Postgres balance
    conn = get_connection()
    balance = 0.0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE -amount END)
                FROM ledger
                WHERE account = 'merchant_payable' AND merchant_id = %s
                """,
                (merchant_id,)
            )
            res = cur.fetchone()[0]
            balance = float(res) if res is not None else 0.0
    finally:
        conn.close()

    # 4. Get audit logs from MongoDB
    logs = get_audit_logs_helper(merchant_id, limit=5)

    return {
        "audit_timestamp": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "merchant_id": merchant_id,
        "merchant_name": merchant_profile.get("name", "Unknown"),
        "status": merchant_profile.get("status", "ACTIVE"),
        "ledger_balance": balance,
        "active_api_keys": active_keys_count,
        "recent_audit_logs": logs
    }