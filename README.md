# PayDesk MCP

PayDesk MCP is a production-grade Model Context Protocol (MCP) server that exposes payment gateway APIs, tools, prompts, and resources. It bridges the gap between an AI assistant (like Claude, Gemini, or ChatGPT) and three underlying database systems to resemble real payment services like Stripe, Razorpay, or PayPal.

---

## Architecture

The server aggregates data across Redis, MongoDB, and PostgreSQL to provide consolidated ledger, dispute, support, and session APIs.

```
       User
         ↓
    AI Assistant
         ↓
  [Bearer Auth JWT]
         ↓
   FastMCP Server
         ↓
 ┌───────┼──────────────┐
 ↓       ↓              ↓
Redis  MongoDB    PostgreSQL
```

- **Redis**: Caches merchant profiles, active developer API keys, dashboard sessions, and rate-limiting buckets.
- **MongoDB**: Stores disputes, support tickets, transaction metadata, and webhook delivery logs.
- **PostgreSQL**: Implements the double-entry accounting ledger, payouts, fee schedules, and balances.

---

## Features

- **Authentication**: JWT Bearer token validation via Starlette HTTP middleware.
- **Authorization & Scoping**: Centralized merchant scoping preventing cross-merchant access.
- **Audit Logging**: Automatic logging of tool execution, arguments, and outcomes into MongoDB.
- **Database Adapters**: Validation of configuration secrets loaded securely via `python-dotenv`.
- **MCP Tools**: 36 specialized tools for key management, dispute evidence, bank payouts, and payment statistics.
- **MCP Resources**: 15 endpoints providing read-only states (e.g. balance, open tickets, system AML/KYC policies).
- **MCP Prompts**: 10 pre-registered system templates aiding LLMs in payment operation diagnostics.

---

## Prerequisites

- **Python**: 3.10 or higher
- **Redis**: 6.x or higher (Listening on port `6379`)
- **MongoDB**: 5.x or higher (Listening on port `27017`)
- **PostgreSQL**: 15.x or higher (Listening on port `5432` with a database named `paydesk`)
- **Git** & **pip**

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone <repository_url>
   cd paydesk-mcp
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # On Windows
   source .venv/bin/activate # On Unix/macOS
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## Configure Environment

1. **Copy the template file**:
   ```bash
   cp .env.example .env
   ```

2. **Fill in your environment credentials** in `.env`:
   Ensure your local PostgreSQL database, username, and password (`JaiBagga02@` by default or custom) are set.

---

## Start Databases

### Option A: Local Native Services
- **Windows Services**: Open `services.msc` and start `MongoDB`, `postgresql-x64-18`, and `Redis` services.
- **WSL/Linux Services**:
  ```bash
  sudo service redis-server start
  sudo service mongodb start
  sudo service postgresql start
  ```

### Option B: Docker Compose
If you prefer Docker, start all three databases using:
```bash
docker run -d --name paydesk-redis -p 6379:6379 redis:alpine
docker run -d --name paydesk-mongo -p 27017:27017 mongo:latest
docker run -d --name paydesk-postgres -p 5432:5432 -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=JaiBagga02@ -e POSTGRES_DB=paydesk postgres:alpine
```

---

## Seed Data

To populate transaction and dispute records:
- **Transactions & Ledger Data**: Run the postgres/mongo seeding script if available (e.g., `seed/seed_malicious_tickets.py` or default database seeders):
  ```bash
  python seed/seed_malicious_tickets.py
  ```
  *Inserts demo support tickets into MongoDB with special payloads to test prompt injection defenses.*

---

## Run Server

Run the server natively:
```bash
python server.py
```
Or run using FastMCP CLI:
```bash
fastmcp run server.py
```

### Expected Startup Output
```text
Starting PayDesk MCP server on 127.0.0.1:8000 with log level 'info'...
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on https://127.0.0.1:8000 (Press CTRL+C to quit)
```

---

## Open MCP Inspector

To test tools and resources inside a visual debugger:
```bash
npx @modelcontextprotocol/inspector fastmcp run server.py
```
This launches a browser-based UI to invoke tools and read resources dynamically.

---

## Example Tool Calls

When interacting with the MCP server, the LLM can invoke these commands:

1. **Get Merchant Balance**:
   - Tool: `get_merchant_balance`
   - Arguments: `{"merchant_id": "MER-1005"}`

2. **Get Recent Transactions**:
   - Tool: `get_recent_transactions`
   - Arguments: `{"merchant_id": "MER-1005", "limit": 5}`

3. **Check Merchant Health**:
   - Tool: `check_merchant_health`
   - Arguments: `{"merchant_id": "MER-1005"}`

4. **Retrieve Support Tickets**:
   - Tool: `get_support_ticket`
   - Arguments: `{"ticket_id": "TCK-501"}`

---

## Troubleshooting

- **Redis connection failed**: Ensure Redis is running and listening on port `6379`. Run `netstat -ano | findstr 6379`.
- **Mongo not running**: Check if the MongoDB service is active. Check `pymongo.errors.ServerSelectionTimeoutError`.
- **Postgres authentication failed**: Check `POSTGRES_USER` and `POSTGRES_PASSWORD` in `.env` and verify PostgreSQL is listening on port `5432`.
- **Missing .env**: Ensure you ran `cp .env.example .env` and didn't leave variables blank. Described startup errors will prevent execution.
- **Port already in use**: Change `MCP_PORT` in `.env` if `8000` is occupied.
