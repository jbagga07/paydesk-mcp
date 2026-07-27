from mcp_app import mcp
from db.mongodb import get_db
from security.auth import get_current_caller
from security.scope import is_authorized
from security.audit import audit_logged, get_audit_logs as get_audit_logs_helper
import datetime
import uuid
from typing import Optional

db = get_db()


@mcp.tool
@audit_logged
def get_transaction_status(txn_id: str):
    """
    Get transaction details by transaction ID.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    txn = db.transactions.find_one(
        {"txn_id": txn_id},
        {"_id": 0}
    )

    if txn is None:
        return {
            "error": f"Transaction '{txn_id}' not found."
        }

    # Validate authorization (derive merchant_id from the found transaction)
    if not is_authorized(context, txn["merchant_id"], required_scope="txn:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access transaction '{txn_id}'."}

    return txn


@mcp.tool
@audit_logged
def get_recent_transactions(merchant_id: str, limit: int = 10):
    """
    Get the latest transactions for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="txn:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access transaction data for merchant '{merchant_id}'."}

    transactions = list(
        db.transactions.find(
            {"merchant_id": merchant_id},
            {"_id": 0}
        )
        .sort("created_at", -1)
        .limit(limit)
    )

    if not transactions:
        return {
            "merchant_id": merchant_id,
            "transactions": [],
            "message": f"No transactions found for merchant '{merchant_id}'."
        }

    return {
        "merchant_id": merchant_id,
        "transactions": transactions
    }


@mcp.tool
@audit_logged
def get_failed_transactions(merchant_id: str, limit: int = 10):
    """
    Get the latest failed transactions for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="txn:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access transaction data for merchant '{merchant_id}'."}

    transactions = list(
        db.transactions.find(
            {
                "merchant_id": merchant_id,
                "status": "FAILED"
            },
            {"_id": 0}
        )
        .sort("created_at", -1)
        .limit(limit)
    )

    if not transactions:
        return {
            "merchant_id": merchant_id,
            "failed_transactions": [],
            "message": f"No failed transactions found for merchant '{merchant_id}'."
        }

    return {
        "merchant_id": merchant_id,
        "failed_transactions": transactions
    }


@mcp.tool
@audit_logged
def get_transaction_count(merchant_id: str):
    """
    Get the total transaction count for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="txn:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access transaction data for merchant '{merchant_id}'."}

    count = db.transactions.count_documents({"merchant_id": merchant_id})

    return {
        "merchant_id": merchant_id,
        "total_transactions": count
    }


@mcp.tool
@audit_logged
def get_success_rate(merchant_id: str):
    """
    Get the transaction success rate statistics for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="txn:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access transaction data for merchant '{merchant_id}'."}

    total_count = db.transactions.count_documents({"merchant_id": merchant_id})

    if total_count == 0:
        return {
            "merchant_id": merchant_id,
            "total_transactions": 0,
            "successful_transactions": 0,
            "success_percentage": 0.0,
            "message": f"No transactions found for merchant '{merchant_id}'."
        }

    successful_count = db.transactions.count_documents(
        {
            "merchant_id": merchant_id,
            "status": {"$in": ["SETTLED", "REFUNDED", "DISPUTED"]}
        }
    )

    success_percentage = (successful_count / total_count) * 100

    return {
        "merchant_id": merchant_id,
        "total_transactions": total_count,
        "successful_transactions": successful_count,
        "success_percentage": round(success_percentage, 2)
    }


@mcp.tool
@audit_logged
def get_daily_transaction_summary(merchant_id: str):
    """
    Get the daily transaction summary (aggregates of count and amount) for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="txn:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access transaction data for merchant '{merchant_id}'."}

    pipeline = [
        {"$match": {"merchant_id": merchant_id}},
        {
            "$project": {
                "date": {"$substr": ["$created_at", 0, 10]},
                "amount": 1,
                "status": 1
            }
        },
        {
            "$group": {
                "_id": "$date",
                "total_transactions": {"$sum": 1},
                "total_amount": {"$sum": "$amount"},
                "successful_transactions": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["SETTLED", "REFUNDED", "DISPUTED"]]},
                            1,
                            0
                        ]
                    }
                },
                "successful_amount": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["SETTLED", "REFUNDED", "DISPUTED"]]},
                            "$amount",
                            0
                        ]
                    }
                },
                "failed_transactions": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "FAILED"]},
                            1,
                            0
                        ]
                    }
                },
                "failed_amount": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "FAILED"]},
                            "$amount",
                            0
                        ]
                    }
                }
            }
        },
        {"$sort": {"_id": -1}}
    ]

    results = list(db.transactions.aggregate(pipeline))

    if not results:
        return {
            "merchant_id": merchant_id,
            "daily_summaries": [],
            "message": f"No transactions found for merchant '{merchant_id}'."
        }

    summaries = []
    for doc in results:
        summaries.append({
            "date": doc["_id"],
            "total_transactions": doc["total_transactions"],
            "total_amount": round(doc["total_amount"], 2),
            "successful_transactions": doc["successful_transactions"],
            "successful_amount": round(doc["successful_amount"], 2),
            "failed_transactions": doc["failed_transactions"],
            "failed_amount": round(doc["failed_amount"], 2)
        })

    return {
        "merchant_id": merchant_id,
        "daily_summaries": summaries
    }


@mcp.tool
@audit_logged
def get_dispute_count(merchant_id: str):
    """
    Get dispute count and status statistics for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="dispute:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access dispute data for merchant '{merchant_id}'."}

    disputes_cursor = db.disputes.find({"merchant_id": merchant_id})
    disputes_list = list(disputes_cursor)

    if not disputes_list:
        return {
            "merchant_id": merchant_id,
            "total_disputes": 0,
            "active_disputes": 0,
            "resolved_disputes": 0,
            "total_dispute_amount": 0.0,
            "by_status": {},
            "message": f"No disputes found for merchant '{merchant_id}'."
        }

    total_count = len(disputes_list)
    total_amount = 0.0
    active_count = 0
    resolved_count = 0
    by_status = {}

    for dispute in disputes_list:
        status = dispute.get("status", "UNKNOWN")
        amount = dispute.get("amount", 0.0)

        total_amount += amount

        # Group count by status
        by_status[status] = by_status.get(status, 0) + 1

        # Classify active vs resolved
        if status in ["OPEN", "UNDER_REVIEW"]:
            active_count += 1
        elif status in ["RESOLVED_CUSTOMER", "RESOLVED_MERCHANT"]:
            resolved_count += 1

    return {
        "merchant_id": merchant_id,
        "total_disputes": total_count,
        "active_disputes": active_count,
        "resolved_disputes": resolved_count,
        "total_dispute_amount": round(total_amount, 2),
        "by_status": by_status
    }


def generate_ticket_id(db) -> str:
    highest = 500
    for doc in db.tickets.find({}, {"ticket_id": 1}):
        tid = doc.get("ticket_id", "")
        if tid.startswith("TCK-"):
            try:
                num = int(tid.split("-")[1])
                if num > highest:
                    highest = num
            except ValueError:
                pass
    return f"TCK-{highest + 1}"


@mcp.tool
@audit_logged
def create_support_ticket(
    merchant_id: str,
    title: str,
    description: str,
    request_id: str,
    approved: bool = False
):
    """
    Create a support ticket for a merchant.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    # Never trust merchant_id from the model for merchant callers
    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    # Validate authorization
    if not is_authorized(context, merchant_id, required_scope="ticket:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to create tickets for merchant '{merchant_id}'."}

    # Approval gate check
    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Ticket creation requires explicit approval. Please review the ticket details and set approved=True to submit.",
            "ticket_details": {
                "merchant_id": merchant_id,
                "title": title,
                "description": description,
                "request_id": request_id
            }
        }

    # Validation
    if not title or not title.strip():
        return {"error": "Validation failed: Title cannot be empty."}
    if not description or not description.strip():
        return {"error": "Validation failed: Description cannot be empty."}
    if not request_id or not request_id.strip():
        return {"error": "Validation failed: Request ID cannot be empty."}
    if not merchant_id or not merchant_id.strip():
        return {"error": "Validation failed: Merchant ID cannot be empty."}

    if len(title) > 100:
        return {"error": "Validation failed: Title exceeds maximum length of 100 characters."}
    if len(description) > 1000:
        return {"error": "Validation failed: Description exceeds maximum length of 1000 characters."}

    # Verify merchant exists in Redis
    from db.redisdb import get_redis
    r = get_redis()
    if not r.exists(f"merchant:{merchant_id}"):
        return {"error": f"Validation failed: Merchant '{merchant_id}' is invalid or does not exist."}

    # Idempotency
    existing_ticket = db.tickets.find_one({"request_id": request_id}, {"_id": 0})
    if existing_ticket:
        return {
            "message": "Ticket already exists (idempotent response)",
            "ticket": existing_ticket
        }

    ticket_id = generate_ticket_id(db)
    created_at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    new_ticket = {
        "ticket_id": ticket_id,
        "merchant_id": merchant_id,
        "title": title,
        "description": description,
        "status": "OPEN",
        "created_at": created_at,
        "request_id": request_id,
        "created_by": context.caller_id
    }

    db.tickets.insert_one(dict(new_ticket))
    new_ticket.pop("_id", None)

    return {
        "message": "Ticket created successfully.",
        "ticket": new_ticket
    }


@mcp.tool
@audit_logged
def get_audit_logs(target_caller_id: Optional[str] = None, limit: int = 20):
    """
    Get audit logs. Only accessible by admins.
    """

    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type != "admin" or context.role != "ADMIN":
        return {"error": "Unauthorized: Only administrators can view audit logs."}

    logs = get_audit_logs_helper(target_caller_id, limit)
    return {
        "logs": logs
    }


@mcp.tool
@audit_logged
def get_dispute_details(dispute_id: str):
    """
    Retrieve dispute details (dispute ID, transaction ID, reason, amount, status, evidence) by dispute ID.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    dispute = db.disputes.find_one({"dispute_id": dispute_id}, {"_id": 0})
    if dispute is None:
        return {"error": f"Dispute '{dispute_id}' not found."}

    if not is_authorized(context, dispute["merchant_id"], required_scope="dispute:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access dispute '{dispute_id}'."}

    return dispute


@mcp.tool
@audit_logged
def update_dispute_evidence(
    dispute_id: str,
    evidence_text: str,
    evidence_url: Optional[str] = None,
    approved: bool = False
):
    """
    Submit evidence text or document URLs to a dispute in MongoDB.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    dispute = db.disputes.find_one({"dispute_id": dispute_id}, {"_id": 0})
    if dispute is None:
        return {"error": f"Dispute '{dispute_id}' not found."}

    if not is_authorized(context, dispute["merchant_id"], required_scope="dispute:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot submit evidence for dispute '{dispute_id}'."}

    if dispute.get("status") not in ["OPEN", "UNDER_REVIEW"]:
        return {"error": f"Validation failed: Dispute is in status '{dispute.get('status')}' and cannot accept evidence."}

    if not evidence_text or not evidence_text.strip():
        return {"error": "Validation failed: Evidence text cannot be empty."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Submitting dispute evidence requires explicit approval. Set approved=True to submit.",
            "details": {
                "dispute_id": dispute_id,
                "evidence_text": evidence_text,
                "evidence_url": evidence_url
            }
        }

    updated_at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    db.disputes.update_one(
        {"dispute_id": dispute_id},
        {
            "$set": {
                "status": "UNDER_REVIEW",
                "evidence_text": evidence_text,
                "evidence_url": evidence_url,
                "evidence_updated_at": updated_at
            }
        }
    )

    return {
        "message": "Dispute evidence updated successfully.",
        "dispute_id": dispute_id,
        "status": "UNDER_REVIEW",
        "evidence_text": evidence_text,
        "evidence_url": evidence_url
    }


@mcp.tool
@audit_logged
def list_merchant_disputes(
    merchant_id: str,
    status: Optional[str] = None,
    limit: int = 10
):
    """
    List all disputes for a merchant, filtered optionally by status.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="dispute:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access dispute data for merchant '{merchant_id}'."}

    query = {"merchant_id": merchant_id}
    if status:
        query["status"] = status

    disputes = list(
        db.disputes.find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )

    return {"merchant_id": merchant_id, "disputes": disputes}


@mcp.tool
@audit_logged
def get_support_ticket(ticket_id: str):
    """
    Fetch a support ticket by ticket ID.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    ticket = db.tickets.find_one({"ticket_id": ticket_id}, {"_id": 0})
    if ticket is None:
        return {"error": f"Support ticket '{ticket_id}' not found."}

    if not is_authorized(context, ticket["merchant_id"], required_scope="ticket:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access ticket '{ticket_id}'."}

    return ticket


@mcp.tool
@audit_logged
def add_ticket_reply(
    ticket_id: str,
    reply_text: str,
    approved: bool = False
):
    """
    Add reply messages from a merchant or admin to a support ticket.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    ticket = db.tickets.find_one({"ticket_id": ticket_id}, {"_id": 0})
    if ticket is None:
        return {"error": f"Support ticket '{ticket_id}' not found."}

    if not is_authorized(context, ticket["merchant_id"], required_scope="ticket:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot reply to ticket '{ticket_id}'."}

    if ticket.get("status") == "CLOSED":
        return {"error": "Validation failed: Cannot reply to a closed ticket."}

    if not reply_text or not reply_text.strip():
        return {"error": "Validation failed: Reply text cannot be empty."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Adding a ticket reply requires explicit approval. Set approved=True to submit.",
            "details": {"ticket_id": ticket_id, "reply_text": reply_text}
        }

    reply = {
        "reply_id": str(uuid.uuid4())[:8],
        "sender_id": context.caller_id,
        "sender_type": context.caller_type,
        "message": reply_text,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }

    db.tickets.update_one(
        {"ticket_id": ticket_id},
        {
            "$push": {"replies": reply},
            "$set": {"status": "OPEN" if context.caller_type == "merchant" else "IN_PROGRESS"}
        }
    )

    return {
        "message": "Reply added successfully.",
        "ticket_id": ticket_id,
        "reply": reply
    }


@mcp.tool
@audit_logged
def update_ticket_status(
    ticket_id: str,
    status: str,
    approved: bool = False
):
    """
    Change ticket status (OPEN, IN_PROGRESS, RESOLVED, CLOSED).
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    ticket = db.tickets.find_one({"ticket_id": ticket_id}, {"_id": 0})
    if ticket is None:
        return {"error": f"Support ticket '{ticket_id}' not found."}

    if not is_authorized(context, ticket["merchant_id"], required_scope="ticket:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot change status for ticket '{ticket_id}'."}

    valid_statuses = ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]
    if status not in valid_statuses:
        return {"error": f"Validation failed: Status must be one of {valid_statuses}"}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Changing ticket status requires explicit approval. Set approved=True to update.",
            "details": {"ticket_id": ticket_id, "status": status}
        }

    db.tickets.update_one(
        {"ticket_id": ticket_id},
        {"$set": {"status": status}}
    )

    return {
        "message": f"Ticket status updated to '{status}' successfully.",
        "ticket_id": ticket_id,
        "status": status
    }


@mcp.tool
@audit_logged
def get_customer_profile(customer_id: str):
    """
    Retrieve customer details from MongoDB collection.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    customer = db.customers.find_one({"customer_id": customer_id}, {"_id": 0})
    if customer is None:
        return {"error": f"Customer '{customer_id}' not found."}

    if not is_authorized(context, customer["merchant_id"], required_scope="customer:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' cannot access customer '{customer_id}'."}

    return customer


@mcp.tool
@audit_logged
def create_customer_profile(
    merchant_id: str,
    email: str,
    name: str,
    phone: Optional[str] = None,
    approved: bool = False
):
    """
    Create a new customer profile in MongoDB.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="customer:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to create customers for merchant '{merchant_id}'."}

    if "@" not in email:
        return {"error": "Validation failed: Email is invalid."}

    if not name or not name.strip():
        return {"error": "Validation failed: Customer name is required."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Creating a customer profile requires explicit approval. Set approved=True to submit.",
            "details": {
                "merchant_id": merchant_id,
                "email": email,
                "name": name,
                "phone": phone
            }
        }

    customer_id = f"cust_{uuid.uuid4().hex[:12]}"
    created_at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    new_customer = {
        "customer_id": customer_id,
        "merchant_id": merchant_id,
        "email": email,
        "name": name,
        "phone": phone,
        "created_at": created_at
    }

    db.customers.insert_one(dict(new_customer))
    new_customer.pop("_id", None)

    return {
        "message": "Customer profile created successfully.",
        "customer": new_customer
    }


@mcp.tool
@audit_logged
def update_customer_profile(
    customer_id: str,
    name: Optional[str] = None,
    phone: Optional[str] = None,
    approved: bool = False
):
    """
    Modify customer profile details in MongoDB.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    customer = db.customers.find_one({"customer_id": customer_id}, {"_id": 0})
    if customer is None:
        return {"error": f"Customer '{customer_id}' not found."}

    if not is_authorized(context, customer["merchant_id"], required_scope="customer:write"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to update customer '{customer_id}'."}

    if not approved:
        return {
            "status": "AWAITING_APPROVAL",
            "message": "Updating customer profile requires explicit approval. Set approved=True to submit.",
            "details": {
                "customer_id": customer_id,
                "name": name,
                "phone": phone
            }
        }

    updates = {}
    if name is not None:
        updates["name"] = name
    if phone is not None:
        updates["phone"] = phone

    if updates:
        db.customers.update_one({"customer_id": customer_id}, {"$set": updates})

    updated_customer = db.customers.find_one({"customer_id": customer_id}, {"_id": 0})
    return {
        "message": "Customer profile updated successfully.",
        "customer": updated_customer
    }


@mcp.tool
@audit_logged
def get_webhook_delivery_logs(merchant_id: str, limit: int = 10):
    """
    Retrieve webhook delivery logs for debugging.
    """
    try:
        context = get_current_caller()
    except Exception as e:
        return {"error": f"Authentication failed: {str(e)}"}

    if context.caller_type == "merchant":
        merchant_id = context.merchant_id

    if not is_authorized(context, merchant_id, required_scope="webhook:read"):
        return {"error": f"Unauthorized: Caller '{context.caller_id}' is not authorized to read webhook logs for merchant '{merchant_id}'."}

    logs = list(
        db.webhook_logs.find({"merchant_id": merchant_id}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )

    return {"merchant_id": merchant_id, "webhook_logs": logs}