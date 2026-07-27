from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP):

    @mcp.prompt
    def summarize_merchant(merchant_id: str):
        """
        Generate a merchant summary.
        """
        return f"""
You are a PayDesk support assistant.

Summarize merchant {merchant_id}.

Use the available MCP tools and resources to gather:

1. Merchant profile
2. Current balance
3. Recent transactions
4. Open disputes (if available)

Produce a concise business summary including:

- Merchant Name
- Merchant Status
- Balance
- Transaction Activity
- Any Risks or Issues
"""

    @mcp.prompt
    def analyze_transaction_flow(merchant_id: str):
        """
        Analyze success rates and diagnose payment checkout failures.
        """
        return f"""
You are a Senior Payment Operations Analyst at PayDesk.

Analyze the checkout transaction flow for merchant {merchant_id}.

Please carry out these investigations:
1. Fetch the overall transaction success rate statistics.
2. Get the daily transaction summary over the last few days to find any sudden drop in volumes.
3. List the most recent failed transactions to extract recurring error codes, gateway responses, or payment method anomalies.

Provide a diagnostic report detailing:
- Current Success Rate status (e.g., Healthy, Degraded, Critical).
- Identifiable patterns of failure (specific card issuers, payment methods like UPI vs Cards, etc.).
- Actionable recommendations to resolve any detected drop-offs.
"""

    @mcp.prompt
    def resolve_customer_dispute(dispute_id: str):
        """
        Guide dispute review, evidence correlation, and resolution draft.
        """
        return f"""
You are a Dispute Specialist.

Review and resolve dispute: {dispute_id}.

Perform the following steps:
1. Retrieve details of the dispute to get the disputed transaction ID, amount, and reason.
2. Retrieve the underlying transaction status and customer profile details.
3. Inspect if the merchant has submitted evidence or comments using dispute tools.
4. Check the system dispute policy to verify the timeline and regulations.

Formulate a recommendation for the merchant:
- If evidence is sufficient, draft a dispute response to submit to the card network.
- If evidence is weak, advise the merchant on what specific documents (receipt, tracking info, signed contract) are missing.
- Outline the direct financial ledger impact of winning vs losing this dispute.
"""

    @mcp.prompt
    def audit_merchant_compliance(merchant_id: str):
        """
        Compliance check on merchant activity, API key changes, and audit logs.
        """
        return f"""
You are an Internal Compliance Auditor at PayDesk.

Conduct a risk and compliance audit on merchant: {merchant_id}.

Verify the following security metrics:
1. Run the administrative compliance audit tool to review profile status, balance, and key counts.
2. Fetch the active API key metadata and check if there are duplicate keys or overly broad scopes.
3. Retrieve recent audit logs to identify any unauthorized tool calls or sudden security updates.
4. Fetch active webhook configuration to check endpoint safety.

Report on:
- Compliance status of merchant's API usage (e.g. key rotations, scopes).
- Any anomalies or suspicious user roles in the audit log history.
- Required corrective actions to align with AML/KYC policies.
"""

    @mcp.prompt
    def generate_accounting_payout_report(merchant_id: str, year: int, month: int):
        """
        Generate monthly accounting ledger, fee earnings, and payout reconciliation reports.
        """
        return f"""
You are a Financial Accountant at PayDesk.

Compile a monthly accounting and settlement report for merchant {merchant_id} for the period {year}-{month:02d}.

Gather the following financial records:
1. Fetch the monthly accounting report to get gross sales, refunds, payouts, and fee income.
2. Retrieve the active balance from the ledger.
3. List the payouts processed in this month to check their status.

Provide a financial breakdown:
- Gross Volume vs Net Merchant Earnings.
- Gateway processing fees collected.
- Reconciled balance (Starting balance + Net Sales - Refunds - Payouts = Current balance).
- Highlight any payout discrepancies or ledger adjustment entries.
"""

    @mcp.prompt
    def diagnose_webhook_failures(merchant_id: str):
        """
        Diagnose broken webhook configurations and failed delivery logs.
        """
        return f"""
You are a Developer Support Engineer.

Diagnose webhook delivery failures for merchant: {merchant_id}.

Inspect the following endpoints:
1. Retrieve the merchant's webhook configuration to verify the target URL and subscribed events.
2. Get the webhook delivery logs to examine failure rates and HTTP response codes.
3. Run the webhook endpoint health tool to calculate overall health status (Healthy, Degraded, Critical).

Produce a developer integration diagnostic report:
- Webhook endpoint status and URL validation.
- Top error codes returned by the merchant's server (e.g. 500 Internal Error, 404 Not Found, Timeout).
- Suggested debugging steps for the merchant's engineering team.
"""

    @mcp.prompt
    def merchant_risk_assessment(merchant_id: str):
        """
        Risk profiling combining disputes, balances, tickets, and operational health.
        """
        return f"""
You are a Risk Officer at PayDesk.

Analyze the operational and financial risk profile of merchant: {merchant_id}.

Conduct a health assessment:
1. Fetch the merchant's health score card.
2. Retrieve active dispute counts and total contested financial volume.
3. Fetch open support ticket lists to check for severe merchant backlog.
4. Retrieve the ledger balance to inspect negative exposure.

Summarize the risk profile:
- Overall risk score and classification (LOW, MEDIUM, HIGH risk).
- Main indicators of risk (high dispute rates, customer complaints backlog, negative balances).
- Recommended action (e.g., hold payouts, request KYC re-verification, or clear for normal operations).
"""

    @mcp.prompt
    def handle_support_escalation(ticket_id: str):
        """
        Review ticket escalation details and draft merchant support replies.
        """
        return f"""
You are a Senior Merchant Support Agent.

Resolve escalated support ticket: {ticket_id}.

Review and gather context:
1. Fetch the ticket details including the description and the history of replies.
2. Look up the merchant's profile and active balance to understand their size/tier.
3. If the ticket mentions a specific transaction or dispute, retrieve its status.

Draft a professional merchant response:
- Acknowledge the issue with clear details.
- Provide direct findings from the transaction/dispute lookup.
- Outline clear next steps to resolve the issue (e.g., waiting for payout clearing, disputing chargeback).
"""

    @mcp.prompt
    def optimize_fee_earnings(merchant_id: str):
        """
        Suggest custom fee schedule optimizations based on volume and payment method stats.
        """
        return f"""
You are a Pricing strategist.

Optimize the fee structure and contract terms for merchant: {merchant_id}.

Analyze:
1. Get the current fee schedule and pricing tier.
2. Retrieve payment method stats to review transaction volume vs fees collected.
3. Review overall transaction summaries to determine monthly growth rates.

Provide a customized pricing proposal:
- Analysis of current payment method efficiency (e.g., high Card fees vs low UPI costs).
- A proposed custom fee schedule (e.g., moving to a premium pricing tier).
- Projected savings for the merchant and gross revenue impact for PayDesk.
"""

    @mcp.prompt
    def customer_loyalty_insights(customer_id: str):
        """
        Evaluate customer lifetime value, metrics, and suggest merchant loyalty campaigns.
        """
        return f"""
You are a Marketing and Customer Analytics Specialist.

Analyze payment behavior and lifetime value for customer: {customer_id}.

Investigate:
1. Retrieve the customer profile details.
2. Retrieve customer lifetime value metrics.
3. Check successful vs failed transactions for this customer.

Provide insights for the merchant:
- Total purchase volume and lifetime value tier (VIP, Active, Occasional).
- Preferred payment methods (e.g. Card, UPI, Netbanking).
- Recommended loyalty campaign, customized discounts, or recovery campaigns for failed transactions.
"""

    @mcp.prompt
    def payout_reconciliation_wizard(merchant_id: str):
        """
        Reconcile settlements and bank payouts with ledger debits.
        """
        return f"""
You are a Treasury Specialist.

Reconcile bank payouts with ledger debits for merchant: {merchant_id}.

Carry out reconciliation:
1. List the merchant's payouts to retrieve settlement IDs, amounts, and dates.
2. Fetch ledger entries to verify the double-entry balancing (debits to merchant_payable vs credits to bank clearing).
3. Retrieve current balance to ensure no pending adjustments.

Draft a reconciliation checklist:
- List of payouts successfully cleared.
- Any payout in PENDING or FAILED status.
- Verification that double-entry balances match perfectly.
"""