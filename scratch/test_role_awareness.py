import sys
import os
import json

# Add current folder to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_client
from demo_client import LLMPlanner, ConversationMemory

def test_role(client_id, query):
    print(f"\n==========================================")
    print(f"Testing CLIENT_ID: {client_id}")
    print(f"Query: '{query}'")
    print(f"==========================================")
    
    # Temporarily override CLIENT_ID in demo_client module
    demo_client.CLIENT_ID = client_id
    
    # Re-initialize planner and mock tools list
    planner = LLMPlanner(model_name=demo_client.OLLAMA_MODEL)
    
    # We will pass a couple of standard tools
    mock_tools = [
        {
            "name": "get_merchant_balance",
            "description": "Retrieve balance of a merchant",
            "inputSchema": {
                "properties": {
                    "merchant_id": {"type": "string"}
                }
            }
        },
        {
            "name": "get_recent_transactions",
            "description": "Retrieve recent transactions for a merchant",
            "inputSchema": {
                "properties": {
                    "merchant_id": {"type": "string"},
                    "limit": {"type": "integer"}
                }
            }
        },
        {
            "name": "get_settlement_records",
            "description": "Retrieve settlement records",
            "inputSchema": {
                "properties": {
                    "merchant_id": {"type": "string"}
                }
            }
        }
    ]
    
    memory = ConversationMemory()
    memory.add_user_message(query)
    
    plan_raw = planner.plan_tool_call(memory, mock_tools)
    print(f"LLM Planned Tool Call JSON:")
    print(plan_raw)
    
    # Test formatting when response is empty/no records found
    mock_empty_result = {
        "status": "success",
        "data": []
    }
    # Add a mock assistant message placeholder as format_final_response expects
    memory.add_assistant_message("placeholder")
    formatted_resp = planner.format_final_response(memory, query, mock_empty_result)
    print(f"LLM Natural Language Response (No records found):")
    print(formatted_resp)

if __name__ == "__main__":
    # Test for Support Agent
    test_role("AGT-01", "Show today's transactions")
    
    # Test for Finance
    test_role("FIN-01", "Show settlements")
    
    # Test for Merchant
    test_role("MER-1001", "Show my transactions")
