from mcp_app import mcp
from db.redisdb import get_redis
from db.mongodb import get_db
from db.postgres import get_connection
from security.auth import get_current_caller
from security.scope import is_authorized, scoped
from security.audit import audit_logged
import uuid
import datetime
import json
from typing import Optional
from decimal import Decimal

redis_client = get_redis()
mongo_db = get_db()


@mcp.tool
@scoped(required_scopes=["txn:read", "ledger:read"], error_msg="Unauthorized: Caller '{caller_id}' cannot access summary data for merchant '{merchant_id}'.")
@audit_logged
def get_merchant_summary(merchant_id: str):
    """
    Get a complete merchant summary by combining data from:
    - Redis (merchant profile)
    - PostgreSQL (merchant balance)
    - MongoDB (recent transactions)
    """
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
@scoped(required_scopes=["txn:write", "ledger:write"])
@audit_logged
def process_refund(
    txn_id: str,
    amount: float,
    reason: str,
    request_id: str,
    approved: bool = False
):
    """
    Process a refund for a transaction. Debits merchant ledger balance and updates transaction status in Mongo.
    """
    context = get_current_caller()

    # 1. Fetch transaction from MongoDB
    txn = mongo_db.transactions.find_one({"txn_id": txn_id}, {"_id": 0})
    if not txn:
        return {"error": f"Transaction '{txn_id}' not found."}

    merchant_id = txn["merchant_id"]
    if not is_authorized(context, merchant_id, required_scopes=["txn:write", "ledger:write"]):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot process refunds for merchant '{merchant_id}'."}

    # Validation
    if not request_id or not request_id.strip():
        return {"error": "Validation failed: Request ID cannot be empty."}

    amount_dec = Decimal(str(amount))

    # Idempotency Check (PostgreSQL)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT txn_id, amount 
                FROM ledger 
                WHERE request_id = %s 
                  AND account = 'merchant_payable'
                LIMIT 1;
                """,
                (request_id,)
            )
            existing_row = cur.fetchone()
            if existing_row:
                conn.close()
                ref_txn = mongo_db.transactions.find_one({"txn_id": existing_row[0]}, {"_id": 0})
                ref_status = ref_txn.get("status") if ref_txn else "REFUNDED"
                return {
                    "message": "Refund already processed (idempotent response)",
                    "txn_id": existing_row[0],
                    "refund_amount": float(existing_row[1]),
                    "status": ref_status
                }
    except Exception:
        pass
    finally:
        conn.close()

    if txn["status"] not in ["CAPTURED", "SETTLED", "PARTIALLY_REFUNDED"]:
        return {"error": f"Validation failed: Transaction with status '{txn['status']}' cannot be refunded."}

    original_amount_dec = Decimal(str(txn["amount"]))
    if amount_dec <= 0 or amount_dec > original_amount_dec:
        return {"error": f"Validation failed: Invalid refund amount. Original: {float(original_amount_dec)}, Requested: {amount}"}

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
            already_refunded = cur.fetchone()[0]
            
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
            current_balance = balance_row[0] if balance_row and balance_row[0] is not None else Decimal("0.0")

        if already_refunded + amount_dec > original_amount_dec:
            conn.close()
            return {"error": f"Validation failed: Total refunded ({float(already_refunded + amount_dec)}) exceeds transaction amount ({float(original_amount_dec)})."}

        if current_balance < amount_dec:
            conn.close()
            return {"error": f"Validation failed: Insufficient merchant balance ({float(current_balance)}) to process refund ({amount})."}

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
                    "available_balance": float(current_balance),
                    "request_id": request_id
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
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at, request_id)
                VALUES (%s, %s, %s, '', 'merchant_payable', 'DEBIT', %s, %s, %s, %s)
                """,
                (entry_id_debit, txn_id, merchant_id, amount_dec, txn["currency"], posted_at, request_id)
            )
            # Credit customer_clearing
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at, request_id)
                VALUES (%s, %s, %s, '', 'customer_clearing', 'CREDIT', %s, %s, %s, %s)
                """,
                (entry_id_credit, txn_id, merchant_id, amount_dec, txn["currency"], posted_at, request_id)
            )
            conn.commit()
    finally:
        conn.close()

    # 4. Update transaction status in MongoDB
    new_status = "REFUNDED" if already_refunded + amount_dec == original_amount_dec else "PARTIALLY_REFUNDED"
    mongo_db.transactions.update_one(
        {"txn_id": txn_id},
        {"$set": {"status": new_status, "refunded_amount": float(already_refunded + amount_dec)}}
    )

    return {
        "message": "Refund processed successfully.",
        "txn_id": txn_id,
        "refund_amount": amount,
        "status": new_status
    }


@mcp.tool
@scoped(required_scopes=["dispute:read", "ledger:read"], error_msg="Unauthorized: Caller '{caller_id}' cannot access chargeback summary for merchant '{merchant_id}'.")
@audit_logged
def get_chargeback_summary(merchant_id: str):
    """
    Correlates dispute records from MongoDB with PostgreSQL ledger entries to show chargeback statistics.
    """
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

    ledger_debits = {r[0]: {"amount": r[1], "currency": r[2]} for r in ledger_rows}

    total_contested_amount = Decimal("0.0")
    active_disputes_count = 0
    financial_impact = Decimal("0.0")

    dispute_list_summary = []
    for d in disputes:
        tid = d.get("txn_id")
        status = d.get("status")
        amt = Decimal(str(d.get("amount", 0.0)))

        total_contested_amount += amt
        if status in ["OPEN", "UNDER_REVIEW"]:
            active_disputes_count += 1

        # Correlate with ledger debit
        impact = ledger_debits.get(tid, {}).get("amount", Decimal("0.0"))
        financial_impact += impact

        dispute_list_summary.append({
            "dispute_id": d.get("dispute_id"),
            "txn_id": tid,
            "status": status,
            "amount": float(amt),
            "ledger_impact": float(impact),
            "currency": d.get("currency", "INR")
        })

    return {
        "merchant_id": merchant_id,
        "total_disputes": len(disputes),
        "active_disputes": active_disputes_count,
        "total_contested_amount": float(total_contested_amount),
        "ledger_debit_impact": float(financial_impact),
        "disputes": dispute_list_summary
    }


@mcp.tool
@scoped(required_scopes=["txn:write", "ledger:write"], error_msg="Unauthorized: Caller is not authorized to create transactions for merchant '{merchant_id}'.")
@audit_logged
def create_transaction(
    merchant_id: str,
    amount: float,
    currency: str,
    payment_method: str,
    request_id: str,
    customer_id: Optional[str] = None,
    approved: bool = False
):
    """
    Simulate creating a new payment transaction. Checks Redis, inserts in Mongo, and creates ledger entries in Postgres.
    """
    # 1. Validate Merchant Profile in Redis
    merchant_profile = redis_client.hgetall(f"merchant:{merchant_id}")
    if not merchant_profile:
        return {"error": f"Validation failed: Merchant '{merchant_id}' does not exist or is inactive in Redis."}

    amount_dec = Decimal(str(amount))
    if amount_dec <= 0:
        return {"error": "Validation failed: Transaction amount must be positive."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Transaction creation requires explicit approval. Set approved=True to submit.",
            "details": {
                "merchant_id": merchant_id,
                "amount": amount,
                "currency": currency,
                "payment_method": payment_method,
                "request_id": request_id
            }
        }

    # Validation
    if not request_id or not request_id.strip():
        return {"error": "Validation failed: Request ID cannot be empty."}

    # Idempotency Check (MongoDB)
    existing_txn = mongo_db.transactions.find_one({"request_id": request_id}, {"_id": 0})
    if existing_txn:
        conn = get_connection()
        fixed_fee = Decimal("0.30")
        pct_fee = Decimal("2.90")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT fixed_fee, percentage_fee FROM fee_schedule WHERE merchant_id = %s", (merchant_id,))
                row = cur.fetchone()
                if row:
                    fixed_fee = row[0]
                    pct_fee = row[1]
        except Exception:
            pass
        finally:
            conn.close()

        fee = Decimal(str(existing_txn.get("fee", 0.0)))
        net_amount = round(Decimal(str(existing_txn["amount"])) - fee, 2)

        return {
            "message": "Transaction already exists (idempotent response)",
            "transaction": existing_txn,
            "fee_calculation": {
                "percentage_rate": f"{float(pct_fee)}%",
                "fixed_rate": float(fixed_fee),
                "calculated_fee": float(fee),
                "net_amount": float(net_amount)
            }
        }

    # 2. Fetch Fee Schedule from Postgres to compute transaction fees
    conn = get_connection()
    fixed_fee = Decimal("0.30")
    pct_fee = Decimal("2.90")
    try:
        with conn.cursor() as cur:
            # check fee schedule table
            cur.execute("SELECT fixed_fee, percentage_fee FROM fee_schedule WHERE merchant_id = %s", (merchant_id,))
            row = cur.fetchone()
            if row:
                fixed_fee = row[0]
                pct_fee = row[1]
    except Exception:
        pass
    finally:
        conn.close()

    # Compute Fee
    fee = (amount_dec * (pct_fee / Decimal("100.0")) + fixed_fee).quantize(Decimal("0.01"))
    net_amount = amount_dec - fee

    txn_id = f"TXN-{uuid.uuid4().hex[:6].upper()}"
    created_at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # 3. Create Transaction Document in MongoDB
    new_txn = {
        "txn_id": txn_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id or "",
        "amount": float(amount_dec),
        "currency": currency,
        "status": "SETTLED",
        "payment_method": payment_method,
        "fee": float(fee),
        "created_at": created_at,
        "request_id": request_id
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
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at, request_id)
                VALUES (%s, %s, %s, '', 'customer_clearing', 'DEBIT', %s, %s, %s, %s)
                """,
                (entry_id_clear, txn_id, merchant_id, amount_dec, currency, posted_at, request_id)
            )
            # Credit merchant_payable
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at, request_id)
                VALUES (%s, %s, %s, '', 'merchant_payable', 'CREDIT', %s, %s, %s, %s)
                """,
                (entry_id_payable, txn_id, merchant_id, net_amount, currency, posted_at, request_id)
            )
            # Credit fee_income
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at, request_id)
                VALUES (%s, %s, %s, '', 'fee_income', 'CREDIT', %s, %s, %s, %s)
                """,
                (entry_id_fee, txn_id, merchant_id, fee, currency, posted_at, request_id)
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "message": "Transaction processed successfully.",
        "transaction": new_txn,
        "fee_calculation": {
            "percentage_rate": f"{float(pct_fee)}%",
            "fixed_rate": float(fixed_fee),
            "calculated_fee": float(fee),
            "net_amount": float(net_amount)
        }
    }


@mcp.tool
@scoped(required_scopes=["txn:write", "ledger:write"])
@audit_logged
def capture_payment(txn_id: str, request_id: str, approved: bool = False):
    """
    Capture a pre-authorized payment. Updates status in Mongo and ledger entries in Postgres.
    """
    context = get_current_caller()

    txn = mongo_db.transactions.find_one({"txn_id": txn_id}, {"_id": 0})
    if not txn:
        return {"error": f"Transaction '{txn_id}' not found."}

    merchant_id = txn["merchant_id"]
    if not is_authorized(context, merchant_id, required_scopes=["txn:write", "ledger:write"]):
        return {"error": f"Unauthorized: Caller is not authorized to capture payments for merchant '{merchant_id}'."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Capture payment requires explicit approval. Set approved=True to capture.",
            "details": {
                "txn_id": txn_id,
                "amount": txn["amount"],
                "merchant_id": merchant_id,
                "request_id": request_id
            }
        }

    # Validation
    if not request_id or not request_id.strip():
        return {"error": "Validation failed: Request ID cannot be empty."}

    # Idempotency Check
    existing_txn = mongo_db.transactions.find_one({"capture_request_id": request_id}, {"_id": 0})
    if existing_txn:
        return {
            "message": "Payment already captured (idempotent response)",
            "txn_id": existing_txn["txn_id"],
            "amount": float(existing_txn["amount"]),
            "status": "CAPTURED"
        }

    if txn["status"] != "AUTHORIZED":
        return {"error": f"Validation failed: Only transactions with AUTHORIZED status can be captured. Current status: '{txn['status']}'."}

    # Fetch fee rate
    conn = get_connection()
    fixed_fee = Decimal("0.30")
    pct_fee = Decimal("2.90")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fixed_fee, percentage_fee FROM fee_schedule WHERE merchant_id = %s", (merchant_id,))
            row = cur.fetchone()
            if row:
                fixed_fee = row[0]
                pct_fee = row[1]
    except Exception:
        pass
    finally:
        conn.close()

    amount_dec = Decimal(str(txn["amount"]))
    fee = (amount_dec * (pct_fee / Decimal("100.0")) + fixed_fee).quantize(Decimal("0.01"))
    net_amount = amount_dec - fee

    # 1. Update Mongo transaction status
    mongo_db.transactions.update_one(
        {"txn_id": txn_id},
        {"$set": {"status": "CAPTURED", "fee": float(fee), "capture_request_id": request_id}}
    )

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
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at, request_id)
                VALUES (%s, %s, %s, '', 'customer_clearing', 'DEBIT', %s, %s, %s, %s)
                """,
                (entry_id_clear, txn_id, merchant_id, amount_dec, txn["currency"], posted_at, request_id)
            )
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at, request_id)
                VALUES (%s, %s, %s, '', 'merchant_payable', 'CREDIT', %s, %s, %s, %s)
                """,
                (entry_id_payable, txn_id, merchant_id, net_amount, txn["currency"], posted_at, request_id)
            )
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at, request_id)
                VALUES (%s, %s, %s, '', 'fee_income', 'CREDIT', %s, %s, %s, %s)
                """,
                (entry_id_fee, txn_id, merchant_id, fee, txn["currency"], posted_at, request_id)
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "message": "Payment captured successfully.",
        "txn_id": txn_id,
        "amount": float(amount_dec),
        "status": "CAPTURED"
    }


@mcp.tool
@scoped(required_scopes=["txn:read", "ledger:read"], error_msg="Unauthorized: Caller is not authorized to read reports for merchant '{merchant_id}'.")
@audit_logged
def get_payment_method_stats(merchant_id: str):
    """
    Aggregate transaction success count from Mongo and fee metrics from Postgres by payment method.
    """
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
            fees_by_txn = {r[0]: r[1] for r in cur.fetchall()}
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
        total_fees = Decimal("0.0")
        for t in txns_with_method:
            total_fees += fees_by_txn.get(t["txn_id"], Decimal("0.0"))

        results.append({
            "payment_method": method,
            "total_transactions": total_count,
            "successful_transactions": successful_count,
            "success_rate": round((successful_count / total_count) * 100, 2) if total_count > 0 else 0.0,
            "total_volume": round(volume, 2),
            "total_fees_incurred": float(total_fees)
        })

    return {"merchant_id": merchant_id, "payment_method_stats": results}


@mcp.tool
@scoped(required_scopes=["txn:read", "ledger:read", "ticket:read", "dispute:read"], error_msg="Unauthorized: Caller is not authorized to audit merchant health.")
@audit_logged
def check_merchant_health(merchant_id: str):
    """
    Risk report combining Redis profile, Postgres balance, and MongoDB disputes/tickets.
    """
    # 1. Redis profile
    merchant_profile = redis_client.hgetall(f"merchant:{merchant_id}")
    if not merchant_profile:
        return {"error": f"Merchant '{merchant_id}' not found."}

    # 2. Postgres balance
    conn = get_connection()
    balance = Decimal("0.0")
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
            balance = res if res is not None else Decimal("0.0")
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

    if balance < Decimal("0.0"):
        health_score -= 25.0
        reasons.append("Deducted 25 pts for negative ledger account balance.")

    health_score = max(0.0, health_score)

    status = "EXCELLENT" if health_score >= 90 else "GOOD" if health_score >= 70 else "NEEDS_ATTENTION" if health_score >= 50 else "HIGH_RISK"

    return {
        "merchant_id": merchant_id,
        "merchant_name": merchant_profile.get("name", "Unknown"),
        "ledger_balance": float(balance),
        "active_disputes": open_disputes,
        "open_tickets": open_tickets,
        "risk_health_score": health_score,
        "risk_status": status,
        "findings": reasons if reasons else ["No issues found. Merchant profile is healthy."]
    }


@mcp.tool
@scoped(required_scopes=["webhook:read"], error_msg="Unauthorized: Caller is not authorized to read webhook health.")
@audit_logged
def get_webhook_endpoint_health(merchant_id: str):
    """
    Get webhook configuration status (Redis) and check delivery failure rate (Mongo).
    """
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
@scoped(required_scopes=["dispute:write", "ledger:write"])
@audit_logged
def resolve_dispute_chargeback(
    dispute_id: str,
    resolution: str,
    approved: bool = False
):
    """
    Resolve dispute document in MongoDB and adjust PostgreSQL ledger holds.
    """
    context = get_current_caller()

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
        amount_dec = Decimal(str(dispute["amount"]))
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
                    (entry_id, dispute["txn_id"], merchant_id, amount_dec, currency, posted_at)
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
        "resolution": resolution,
        "chargeback_reversed": chargeback_reversed
    }


@mcp.tool
@scoped(required_scopes=["customer:read", "ledger:read"])
@audit_logged
def get_customer_lifetime_value(customer_id: str):
    """
    Calculate customer purchase volumes, fee summaries, and metrics across Mongo and Postgres.
    """
    context = get_current_caller()

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
    total_spent = Decimal("0.0")
    total_fees = Decimal("0.0")
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
                    total_spent = res[0] if res[0] is not None else Decimal("0.0")
                    total_fees = res[1] if res[1] is not None else Decimal("0.0")
    finally:
        conn.close()

    total_gross = total_spent + total_fees

    return {
        "customer_id": customer_id,
        "customer_name": customer.get("name"),
        "customer_email": customer.get("email"),
        "total_transactions": len(txns),
        "successful_transactions": sum(1 for t in txns if t["status"] in ["SETTLED", "CAPTURED"]),
        "lifetime_gross_spent": float(total_gross),
        "lifetime_net_merchant_volume": float(total_spent),
        "total_gateway_fees": float(total_fees)
    }


@mcp.tool
@scoped(admin_only=True, admin_only_msg="Unauthorized: Only administrators can execute compliance audits.")
@audit_logged
def admin_audit_merchant(merchant_id: str):
    """
    Compliance audit tool correlating audit logs (Mongo), keys (Redis), and ledger stats (Postgres). Only accessible by admins.
    """
    # 1. Get profile from Redis
    merchant_profile = redis_client.hgetall(f"merchant:{merchant_id}")
    if not merchant_profile:
        return {"error": f"Merchant '{merchant_id}' not found."}

    # 2. Get API keys from Redis
    keys_data = redis_client.hgetall(f"merchant:{merchant_id}:api_keys")
    active_keys_count = len(keys_data)

    # 3. Get Postgres balance
    conn = get_connection()
    balance = Decimal("0.0")
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
            balance = res if res is not None else Decimal("0.0")
    finally:
        conn.close()

    # 4. Get audit logs from MongoDB
    logs = get_audit_logs_helper(merchant_id, limit=5)

    return {
        "audit_timestamp": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "merchant_id": merchant_id,
        "merchant_name": merchant_profile.get("name", "Unknown"),
        "status": merchant_profile.get("status", "ACTIVE"),
        "ledger_balance": float(balance),
        "active_api_keys": active_keys_count,
        "recent_audit_logs": logs
    }