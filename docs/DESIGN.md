# PayDesk MCP - System Design Document

This document details the architectural decisions, database models, request flows, and security mechanics of the **PayDesk MCP** platform.

---

## 1. Project Goals

- **Production-Grade Simulation**: Model a real-world payment gateway platform (like Stripe or PayPal) for developer testing, LLM interactions, and automated operations.
- **Polyglot Persistence**: Demonstrate clear boundary isolation by assigning distinct data concerns to the database engines best suited for them (Redis, MongoDB, PostgreSQL).
- **Zero-Trust Scoping**: Prevent cross-tenant data leaks and prompt injection bypasses at the application layer.
- **Auditable Execution**: Automatically track every tool call, input parameter, execution status, and database result.

---

## 2. Request Lifecycle Flow

Every call from the LLM client progresses through the following layers before hitting a database or returning a response:

```
[LLM / MCP Client]
       ↓
[FastMCP Transport]  (Reads JSON-RPC request)
       ↓
[Authentication]     (BearerAuthMiddleware decodes JWT and binds context)
       ↓
[Authorization]      (is_authorized checks required scopes: ledger, dispute, etc.)
       ↓
[Audit Decorator]    (audit_logged generates uuid correlation ID)
       ↓
[Scoping / Override] (Forces query scoping: merchant callers restricted to self)
       ↓
[Database Execution] (Queries Redis, MongoDB, and/or PostgreSQL)
       ↓
[Audit Log Write]    (Logs result, execution status, and timestamp to Mongo)
       ↓
[JSON-RPC Response]
```

---

## 3. Directory Layout

- [server.py](file:///d:/OneDrive/Desktop/paydesk-mcp/server.py): Main entry point launching the Starlette HTTP server and Uvicorn.
- [mcp_app.py](file:///d:/OneDrive/Desktop/paydesk-mcp/mcp_app.py): Initializes the `FastMCP` application.
- [db/](file:///d:/OneDrive/Desktop/paydesk-mcp/db): Database adapters that load credentials from `.env` and validate connections.
  - [redisdb.py](file:///d:/OneDrive/Desktop/paydesk-mcp/db/redisdb.py)
  - [mongodb.py](file:///d:/OneDrive/Desktop/paydesk-mcp/db/mongodb.py)
  - [postgres.py](file:///d:/OneDrive/Desktop/paydesk-mcp/db/postgres.py)
- [security/](file:///d:/OneDrive/Desktop/paydesk-mcp/security): Security logic.
  - [auth.py](file:///d:/OneDrive/Desktop/paydesk-mcp/security/auth.py): Decodes tokens, binds `CallerContext` contextvars, generates self-signed TLS certificates.
  - [scope.py](file:///d:/OneDrive/Desktop/paydesk-mcp/security/scope.py): Scoping validation rules.
  - [audit.py](file:///d:/OneDrive/Desktop/paydesk-mcp/security/audit.py): Auditing decorator logging to MongoDB.
- [tools/](file:///d:/OneDrive/Desktop/paydesk-mcp/tools): 36 tools grouped by storage backends.
- [prompts/](file:///d:/OneDrive/Desktop/paydesk-mcp/prompts): 10 prompts for transaction analysis, troubleshooting, and support workflows.
- [resources/](file:///d:/OneDrive/Desktop/paydesk-mcp/resources): 15 read-only data resources.
- [seed/](file:///d:/OneDrive/Desktop/paydesk-mcp/seed): Data seeding scripts.
- [tests/](file:///d:/OneDrive/Desktop/paydesk-mcp/tests) / verify files: Test suites validating endpoints, auth middleware, scopes, and tools.

---

## 4. Polyglot Persistence Strategy

```
┌─────────────────────────┐     ┌──────────────────────────┐     ┌─────────────────────────┐
│     Redis (Cache)       │     │    PostgreSQL (SQL)      │     │     MongoDB (NoSQL)     │
├─────────────────────────┤     ├──────────────────────────┤     ├─────────────────────────┤
│ - Merchant Profiles     │     │ - Ledger (Double-Entry)  │     │ - Disputes Documents    │
│ - Developer API Keys    │     │ - Settlement Payouts     │     │ - Support Tickets       │
│ - Dashboard Sessions    │     │ - Fee Schedules          │     │ - Webhook Attempt Logs  │
│ - API Rate Limits       │     │ - Account Balances       │     │ - Transactions metadata │
└─────────────────────────┘     └──────────────────────────┘     └─────────────────────────┘
```

### Why Redis?
Ideal for high-throughput, low-latency key-value storage. 
- **Session Management**: Session timeouts can leverage Redis TTLs automatically.
- **Rate-limiting**: API key query counters are checked in milliseconds using Redis key increments.
- **Auth Caching**: Merchant status is cached to avoid querying Postgres on every token authorization check.

### Why PostgreSQL?
Critical for transactional integrity (ACID compliance) and double-entry accounting.
- **Ledger Entries**: Balancing debits and credits must be absolutely consistent. A failure in one leg of a transfer must roll back the other.
- **Payout Audits**: Precise mathematical summaries using standard SQL queries prevent balance duplication or loss.

### Why MongoDB?
Optimized for unstructured, nested, or document-centric records.
- **Disputes**: Disputes contain dynamic evidence attachments, messages from card networks, and variable status histories.
- **Support Tickets**: Support threads are logged as a single document with nested lists of messages, replies, and logs.
- **Webhook Logs**: Webhook response codes and request payloads have varying sizes and shapes.

---

## 5. Cross-Database Schema & Joins

Because data is distributed across multiple engines, **joins are executed at the application layer**. The server queries primary keys (like `merchant_id`, `txn_id`, or `customer_id`) from one store and utilizes them as foreign keys to retrieve corresponding items in the other stores.

### Entity-Relationship Diagram (Cross-Database)

```
  [Redis]                       [MongoDB]                         [PostgreSQL]
  
  Merchant (HSET)               Transaction (Doc)                 Ledger (Table)
  - merchant_id (PK) ──┐        - txn_id (PK) ◄────────────────── - entry_id (PK)
                       │        - merchant_id (FK) ────────────── - txn_id (FK)
                       ├───────►- customer_id (FK)                - merchant_id (FK)
                       │                                          - settlement_id (FK)
                       │        Disputes (Doc)
                       │        - dispute_id (PK)                 Fee Schedule (Table)
                       ├───────►- txn_id (FK)                     - merchant_id (PK)
                       │        - merchant_id (FK)
                       │
                       │        Support Tickets (Doc)
                       └───────►- ticket_id (PK)
                                - merchant_id (FK)
```

### Join Example: `get_merchant_summary(merchant_id)`
1. Read profile metadata (like merchant name and business status) from **Redis** hash `merchant:{merchant_id}`.
2. Sum matching credits and debits in the **PostgreSQL** `ledger` table where `merchant_id = {merchant_id}` and `account = 'merchant_payable'` to compute the current payable balance.
3. List the 5 most recent transaction documents from the **MongoDB** `transactions` collection matching `merchant_id = {merchant_id}`.
4. Merge these responses into a single JSON object and return it.

---

## 6. Architecture Benefits

- **Scalability**: Databases can be scaled independently. If transaction query volume spikes, MongoDB can be scaled horizontally without affecting PostgreSQL ledger processing.
- **Performance**: High-frequency auth checks hitting Redis bypass SQL completely, preserving PostgreSQL resources for payout settlements.
- **Security Isolation**: API token metadata resides in Redis, accounting entries in a secure PostgreSQL server, and user logs in MongoDB. If one database experiences a breach, the others remain isolated.
- **Modularity**: Code adapters for PostgreSQL can be modified or migrated without touching MongoDB or Redis adapters.
