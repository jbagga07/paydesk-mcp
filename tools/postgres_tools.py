from mcp_app import mcp
from db.postgres import get_connection
from security.auth import get_current_caller
from security.scope import is_authorized
from security.audit import audit_logged
import uuid
import datetime
from typing import Optional


@mcp.tool
@audit_logged
def get_merchant_balance(merchant_id: str):
    """
    Get the total payable balance for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="ledger:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to access ledger data for merchant '{merchant_id}'."}

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT merchant_id,
                       COALESCE(SUM(amount), 0) AS balance
                FROM ledger
                WHERE account = 'merchant_payable'
                  AND merchant_id = %s
                GROUP BY merchant_id;
                """,
                (merchant_id,),
            )

            result = cur.fetchone()

    finally:
        conn.close()

    if result is None:
        return {
            "message": "Merchant not found"
        }

    return {
        "merchant_id": result[0],
        "balance": float(result[1])
    }


@mcp.tool
@audit_logged
def get_merchant_settlements(merchant_id: str):
    """
    Get recent settlements for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="ledger:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to access ledger data for merchant '{merchant_id}'."}

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT settlement_id,
                       COUNT(DISTINCT txn_id) AS txn_count,
                       SUM(CASE WHEN account = 'customer_clearing' THEN amount ELSE 0 END) AS gross_amount,
                       SUM(CASE WHEN account = 'merchant_payable' THEN amount ELSE 0 END) AS net_amount,
                       SUM(CASE WHEN account = 'fee_income' THEN amount ELSE 0 END) AS fee_amount,
                       MIN(posted_at) AS settled_at,
                       currency
                FROM ledger
                WHERE merchant_id = %s
                  AND settlement_id IS NOT NULL
                  AND settlement_id != ''
                GROUP BY settlement_id, currency
                ORDER BY settled_at DESC;
                """,
                (merchant_id,),
            )

            rows = cur.fetchall()

    finally:
        conn.close()

    if not rows:
        return {
            "merchant_id": merchant_id,
            "settlements": [],
            "message": f"No settlements found for merchant '{merchant_id}'."
        }

    settlements = []
    for row in rows:
        settlements.append({
            "settlement_id": row[0],
            "transaction_count": int(row[1]),
            "gross_amount": float(row[2]),
            "net_amount": float(row[3]),
            "fee_amount": float(row[4]),
            "settled_at": row[5].isoformat(),
            "currency": row[6]
        })

    return {
        "merchant_id": merchant_id,
        "settlements": settlements
    }


@mcp.tool
@audit_logged
def get_ledger_entries(merchant_id: str, limit: int = 20):
    """
    Get the latest ledger entries for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="ledger:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to access ledger data for merchant '{merchant_id}'."}

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entry_id,
                       txn_id,
                       settlement_id,
                       account,
                       direction,
                       amount,
                       currency,
                       posted_at
                FROM ledger
                WHERE merchant_id = %s
                ORDER BY posted_at DESC
                LIMIT %s;
                """,
                (merchant_id, limit),
            )

            rows = cur.fetchall()

    finally:
        conn.close()

    if not rows:
        return {
            "merchant_id": merchant_id,
            "ledger_entries": [],
            "message": f"No ledger entries found for merchant '{merchant_id}'."
        }

    entries = []
    for row in rows:
        entries.append({
            "entry_id": row[0],
            "txn_id": row[1],
            "settlement_id": row[2],
            "account": row[3],
            "direction": row[4],
            "amount": float(row[5]),
            "currency": row[6],
            "posted_at": row[7].isoformat()
        })

    return {
        "merchant_id": merchant_id,
        "ledger_entries": entries
    }


def ensure_fee_schedule_table(conn):
    """
    Ensure the fee_schedule table exists in PostgreSQL.
    """
    with conn.cursor() as cur:
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


@mcp.tool
@audit_logged
def create_settlement_payout(
    merchant_id: str,
    amount: float,
    currency: str,
    approved: bool = False
):
    """
    Initiate a settlement payout for a merchant. Transfers funds from merchant_payable.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="ledger:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to write ledger data for merchant '{merchant_id}'."}

    if amount <= 0:
        return {"error": "Validation failed: Payout amount must be greater than zero."}

    # Fetch net payable balance
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 
                    SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE -amount END)
                FROM ledger
                WHERE account = 'merchant_payable'
                  AND merchant_id = %s
                """,
                (merchant_id,),
            )
            balance_row = cur.fetchone()
            current_balance = float(balance_row[0]) if balance_row and balance_row[0] is not None else 0.0

        if amount > current_balance:
            conn.close()
            return {"error": f"Validation failed: Insufficient balance. Available: {current_balance}, Requested: {amount}"}

        if not approved:
            conn.close()
            return {
                "status": "AWAITING_APPROVAL",
                "message": "Payout creation requires explicit approval. Set approved=True to submit.",
                "details": {
                    "merchant_id": merchant_id,
                    "amount": amount,
                    "currency": currency,
                    "available_balance": current_balance
                }
            }

        # Create ledger entries for payout
        settlement_id = f"STL-{uuid.uuid4().hex[:6].upper()}"
        entry_id_debit = f"LED-{uuid.uuid4().hex[:6].upper()}"
        entry_id_credit = f"LED-{uuid.uuid4().hex[:6].upper()}"
        posted_at = datetime.datetime.now()

        with conn.cursor() as cur:
            # Debit merchant payable
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, '', %s, %s, 'merchant_payable', 'DEBIT', %s, %s, %s)
                """,
                (entry_id_debit, merchant_id, settlement_id, amount, currency, posted_at)
            )
            # Credit bank clearing
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, '', %s, %s, 'payout_clearing', 'CREDIT', %s, %s, %s)
                """,
                (entry_id_credit, merchant_id, settlement_id, amount, currency, posted_at)
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "message": "Payout initiated successfully.",
        "settlement_id": settlement_id,
        "amount": amount,
        "currency": currency,
        "status": "PAID"
    }


@mcp.tool
@audit_logged
def get_payout_details(payout_id: str):
    """
    Retrieve payout details by settlement/payout ID.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at
                FROM ledger
                WHERE settlement_id = %s
                """,
                (payout_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return {"error": f"Payout '{payout_id}' not found."}

    # Verify scope for the first merchant_id resolved
    merchant_id = rows[0][2]
    if not is_authorized(context, merchant_id, required_scope="ledger:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to read payout details."}

    entries = []
    total_amount = 0.0
    currency = ""
    for r in rows:
        entries.append({
            "entry_id": r[0],
            "txn_id": r[1],
            "account": r[4],
            "direction": r[5],
            "amount": float(r[6]),
            "currency": r[7],
            "posted_at": r[8].isoformat()
        })
        if r[4] == 'merchant_payable':
            total_amount = float(r[6])
            currency = r[7]

    return {
        "payout_id": payout_id,
        "merchant_id": merchant_id,
        "amount": total_amount,
        "currency": currency,
        "entries": entries
    }


@mcp.tool
@audit_logged
def list_merchant_payouts(merchant_id: str, limit: int = 10):
    """
    List all payouts (settlements) processed for a merchant.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="ledger:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to read ledger data for merchant '{merchant_id}'."}

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
                  AND settlement_id IS NOT NULL
                  AND settlement_id != ''
                GROUP BY settlement_id, currency
                ORDER BY MIN(posted_at) DESC
                LIMIT %s;
                """,
                (merchant_id, limit),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    payouts = []
    for r in rows:
        payouts.append({
            "payout_id": r[0],
            "amount": float(r[1]),
            "currency": r[2],
            "created_at": r[3].isoformat()
        })

    return {"merchant_id": merchant_id, "payouts": payouts}


@mcp.tool
@audit_logged
def get_fee_schedule(merchant_id: str):
    """
    Retrieve active fee pricing structure for a merchant.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="ledger:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to view fee schedule for merchant '{merchant_id}'."}

    conn = get_connection()
    try:
        ensure_fee_schedule_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pricing_tier, fixed_fee, percentage_fee, updated_at FROM fee_schedule WHERE merchant_id = %s", (merchant_id,))
            row = cur.fetchone()
            
            if not row:
                # Seed a default schedule
                cur.execute(
                    "INSERT INTO fee_schedule (merchant_id, pricing_tier, fixed_fee, percentage_fee) VALUES (%s, 'STANDARD', 0.30, 2.90)",
                    (merchant_id,)
                )
                conn.commit()
                row = ("STANDARD", 0.30, 2.90, datetime.datetime.now())
    finally:
        conn.close()

    return {
        "merchant_id": merchant_id,
        "pricing_tier": row[0],
        "fixed_fee": float(row[1]),
        "percentage_fee": float(row[2]),
        "updated_at": row[3].isoformat()
    }


@mcp.tool
@audit_logged
def update_fee_schedule(
    merchant_id: str,
    pricing_tier: str,
    fixed_fee: float,
    percentage_fee: float,
    approved: bool = False
):
    """
    Update fee schedules for a merchant. Only accessible by admins.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type != "admin" or context.role != "ADMIN":
        return {"error": "Unauthorized: Only administrators can update fee schedules."}

    if not is_authorized(context, merchant_id, required_scope="ledger:write"):
        return {"error": f"Unauthorized: Caller lacks scopes to update ledger settings for '{merchant_id}'."}

    if fixed_fee < 0 or percentage_fee < 0:
        return {"error": "Validation failed: Fees cannot be negative."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Updating fee schedule requires explicit approval. Set approved=True to submit.",
            "details": {
                "merchant_id": merchant_id,
                "pricing_tier": pricing_tier,
                "fixed_fee": fixed_fee,
                "percentage_fee": percentage_fee
            }
        }

    conn = get_connection()
    try:
        ensure_fee_schedule_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fee_schedule (merchant_id, pricing_tier, fixed_fee, percentage_fee, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (merchant_id) DO UPDATE
                SET pricing_tier = EXCLUDED.pricing_tier,
                    fixed_fee = EXCLUDED.fixed_fee,
                    percentage_fee = EXCLUDED.percentage_fee,
                    updated_at = EXCLUDED.updated_at
                """,
                (merchant_id, pricing_tier, fixed_fee, percentage_fee, datetime.datetime.now())
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "message": "Fee schedule updated successfully.",
        "merchant_id": merchant_id,
        "pricing_tier": pricing_tier,
        "fixed_fee": fixed_fee,
        "percentage_fee": percentage_fee
    }


@mcp.tool
@audit_logged
def get_monthly_accounting_report(merchant_id: str, year: int, month: int):
    """
    Retrieve monthly aggregate sales, refunds, payouts, and fees for accounting.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="ledger:read"):
        return {"error": f"Unauthorized: Caller is not authorized to read ledger reports for merchant '{merchant_id}'."}

    conn = get_connection()
    try:
        start_date = datetime.datetime(year, month, 1)
        if month == 12:
            end_date = datetime.datetime(year + 1, 1, 1)
        else:
            end_date = datetime.datetime(year, month + 1, 1)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 
                    SUM(CASE WHEN account = 'merchant_payable' AND direction = 'CREDIT' THEN amount ELSE 0 END) AS gross_sales,
                    SUM(CASE WHEN account = 'merchant_payable' AND direction = 'DEBIT' AND (settlement_id IS NULL OR settlement_id = '') THEN amount ELSE 0 END) AS refunds_adjustments,
                    SUM(CASE WHEN account = 'merchant_payable' AND direction = 'DEBIT' AND settlement_id IS NOT NULL AND settlement_id != '' THEN amount ELSE 0 END) AS payouts,
                    SUM(CASE WHEN account = 'fee_income' THEN amount ELSE 0 END) AS fee_income
                FROM ledger
                WHERE merchant_id = %s
                  AND posted_at >= %s
                  AND posted_at < %s
                """,
                (merchant_id, start_date, end_date),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    return {
        "merchant_id": merchant_id,
        "year": year,
        "month": month,
        "gross_sales": float(row[0]) if row[0] is not None else 0.0,
        "refunds_and_adjustments": float(row[1]) if row[1] is not None else 0.0,
        "payouts": float(row[2]) if row[2] is not None else 0.0,
        "fee_income": float(row[3]) if row[3] is not None else 0.0
    }


@mcp.tool
@audit_logged
def adjust_ledger_balance(
    merchant_id: str,
    amount: float,
    currency: str,
    description: str,
    approved: bool = False
):
    """
    Create manual balance adjustments on the ledger. Only accessible by admins.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type != "admin" or context.role != "ADMIN":
        return {"error": "Unauthorized: Only administrators can create balance adjustments."}

    if not is_authorized(context, merchant_id, required_scope="ledger:write"):
        return {"error": f"Unauthorized: Caller lacks scope to write ledger adjustments for '{merchant_id}'."}

    if not description or not description.strip():
        return {"error": "Validation failed: Adjustment description cannot be empty."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Manual ledger adjustment requires explicit approval. Set approved=True to submit.",
            "details": {
                "merchant_id": merchant_id,
                "amount": amount,
                "currency": currency,
                "description": description
            }
        }

    direction = "CREDIT" if amount >= 0 else "DEBIT"
    abs_amount = abs(amount)

    conn = get_connection()
    try:
        entry_id = f"LED-ADJ-{uuid.uuid4().hex[:6].upper()}"
        posted_at = datetime.datetime.now()

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ledger (entry_id, txn_id, merchant_id, settlement_id, account, direction, amount, currency, posted_at)
                VALUES (%s, %s, %s, '', 'merchant_payable', %s, %s, %s, %s)
                """,
                (entry_id, f"ADJ-{description[:20]}", merchant_id, direction, abs_amount, currency, posted_at)
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "message": "Ledger balance adjusted successfully.",
        "entry_id": entry_id,
        "amount": amount,
        "direction": direction,
        "currency": currency,
        "description": description
    }


@mcp.tool
@audit_logged
def get_chargeback_financials(merchant_id: str):
    """
    Retrieve all chargeback financial holds/debits for a merchant.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="ledger:read"):
        return {"error": f"Unauthorized: Caller is not authorized to read ledger data for merchant '{merchant_id}'."}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Query debits to merchant_payable that are not payouts and are related to chargebacks
            cur.execute(
                """
                SELECT entry_id, txn_id, amount, currency, posted_at
                FROM ledger
                WHERE merchant_id = %s
                  AND account = 'merchant_payable'
                  AND direction = 'DEBIT'
                  AND (settlement_id IS NULL OR settlement_id = '')
                  AND (txn_id LIKE 'TXN%' OR txn_id LIKE 'DISP%')
                ORDER BY posted_at DESC;
                """,
                (merchant_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    chargebacks = []
    for r in rows:
        chargebacks.append({
            "entry_id": r[0],
            "transaction_id": r[1],
            "amount": float(r[2]),
            "currency": r[3],
            "posted_at": r[4].isoformat()
        })

    return {"merchant_id": merchant_id, "chargebacks": chargebacks}