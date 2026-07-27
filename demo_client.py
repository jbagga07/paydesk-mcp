import os
import sys
import json
import urllib3
import requests
import ollama
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

# Import rich elements for premium terminal styling
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

# Add current folder to path to import project security credentials
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Disable SSL verification warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =====================================================================
# Configuration Settings
# =====================================================================
# CLIENT CONFIGURATION (Change this value to switch caller)
# =====================================================================
CLIENT_ID: str = "MER-1005"  # <-- USER CONFIGURATION (e.g. MER-1005, ADM-01, AGT-01, etc.)
# =====================================================================

SERVER_URL: str = "https://127.0.0.1:8000/mcp"
OLLAMA_MODEL: str = "gemma4:31b-cloud"  # Adjust model name as needed for local setup
VERIFY_SSL: bool = False
TIMEOUT: int = 30

# Dynamically generate token using backend files
try:
    from security.auth import generate_token
    JWT_TOKEN: str = generate_token(CLIENT_ID, expires_in_seconds=86400)
except Exception:
    # Fallback to loading from env file if import fails
    import jwt
    import datetime
    import os
    from dotenv import load_dotenv
    load_dotenv()
    JWT_SECRET = os.getenv("JWT_SECRET", "paydesk_jwt_secret_key_2026_safe_and_long_enough")
    payload = {
        "sub": CLIENT_ID,
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=86400)
    }
    JWT_TOKEN = jwt.encode(payload, JWT_SECRET, algorithm="HS256")


# =====================================================================
# Classes
# =====================================================================

class Logger:
    """Handles beautiful, rich color terminal logging for steps 1-7."""

    def __init__(self) -> None:
        self.console = Console()

    def log_step(self, step_num: int, title: str, content: Any, is_json: bool = False) -> None:
        """Prints a styled step header and contents to the terminal."""
        self.console.print()
        self.console.print(f"[bold cyan]=========================[/bold cyan]")
        self.console.print(f"[bold cyan]STEP {step_num}: {title}[/bold cyan]")
        self.console.print(f"[bold cyan]=========================[/bold cyan]")
        self.console.print()
        
        if is_json:
            try:
                if isinstance(content, str):
                    parsed = json.loads(content)
                else:
                    parsed = content
                formatted_json = json.dumps(parsed, indent=4)
                syntax = Syntax(formatted_json, "json", theme="monokai", background_color="default")
                self.console.print(syntax)
            except Exception:
                self.console.print(str(content))
        else:
            self.console.print(str(content))
        self.console.print()

    def log_error(self, message: str) -> None:
        """Prints an error message to the terminal with high-visibility styling."""
        self.console.print(f"[bold red]ERROR: {message}[/bold red]")

    def print_header(self, server_url: str, authenticated_as: str, llm_model: str) -> None:
        """Displays the startup panel detailing connectivity parameters."""
        header_text = Text()
        header_text.append("=====================================\n", style="bold green")
        header_text.append("PayDesk AI Assistant (Interactive CLI)\n", style="bold white")
        header_text.append("=====================================\n", style="bold green")
        header_text.append(f"Connected to:\n  {server_url}\n\n", style="cyan")
        header_text.append(f"Authenticated as:\n  {authenticated_as}\n\n", style="cyan")
        header_text.append(f"LLM Model:\n  {llm_model}\n\n", style="cyan")
        header_text.append('Type "exit" or "quit" to quit.\n', style="italic yellow")
        
        self.console.print(Panel(header_text, border_style="green", expand=False))


@dataclass
class ConversationMemory:
    """Manages the conversation history stack for context-aware interactions."""

    history: List[Dict[str, str]] = field(default_factory=list)

    def add_user_message(self, message: str) -> None:
        """Adds a user message to the memory stack."""
        self.history.append({"role": "user", "content": message})

    def add_assistant_message(self, message: str) -> None:
        """Adds an assistant response to the memory stack."""
        self.history.append({"role": "assistant", "content": message})

    def add_system_message(self, message: str) -> None:
        """Adds a system message or metadata descriptor to the stack."""
        self.history.append({"role": "system", "content": message})

    def get_messages(self) -> List[Dict[str, str]]:
        """Returns the full message list for Ollama request payloads."""
        return self.history

    def pop_last(self) -> None:
        """Removes the last message in case of an error recovery loop."""
        if self.history:
            self.history.pop()


class LLMPlanner:
    """Interacts with Ollama to select appropriate tools and format responses."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def plan_tool_call(self, memory: ConversationMemory, tools: List[Dict[str, Any]]) -> str:
        """
        Queries Ollama to decide which MCP tool to run and formats arguments.
        Returns the raw tool call payload string generated by the LLM.
        """
        # Format list of available tools to supply to the LLM system instructions
        tool_descriptions = []
        for t in tools:
            tool_descriptions.append({
                "name": t.get("name"),
                "description": t.get("description"),
                "input_schema": t.get("inputSchema", {})
            })

        system_instruction = f"""You are the planning engine for a PayDesk MCP client.
You are connected to an MCP server exposing internal databases for merchant operations.
You NEVER answer user questions from memory. You must always select an MCP tool to fetch the required information.
Your job is to select the appropriate tool and arguments based on the user's query and the conversation history.

Available MCP Tools:
{json.dumps(tool_descriptions, indent=2)}

Rules:
1. You must select exactly ONE tool from the list above.
2. Return ONLY a valid JSON object. No explanations, no markdown formatting (do not wrap in ```json), no text outside the JSON.
3. The JSON must match this exact schema:
{{
    "tool": "tool_name",
    "arguments": {{
        "arg_name": "arg_value"
    }}
}}
If no tool is suitable or needed, return:
{{
    "tool": null,
    "arguments": {{}}
}}

Examples:
User: "What's my balance?"
Your Response:
{{"tool": "get_merchant_balance", "arguments": {{"merchant_id": "MER-1005"}}}}

User: "Check transaction TXN-20001"
Your Response:
{{"tool": "get_transaction_status", "arguments": {{"txn_id": "TXN-20001"}}}}
"""

        # Construct payload with system instructions first
        messages = [{"role": "system", "content": system_instruction}]
        messages.extend(memory.get_messages())

        response = ollama.chat(
            model=self.model_name,
            messages=messages,
            options={"temperature": 0.0}  # Deterministic planning
        )
        
        content = response["message"]["content"].strip()
        
        # Strip markdown syntax wraps if output by the LLM
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            
        return content

    def format_final_response(self, memory: ConversationMemory, user_query: str, raw_tool_result: Dict[str, Any]) -> str:
        """
        Asks Ollama to summarize the raw tool outputs into a friendly response.
        """
        system_instruction = """You are PayDesk, a helpful, precise financial support AI assistant.
Your job is to read the raw database response (JSON) from the MCP tool call and formulate a clear, professional, and friendly answer to the user's query.
- Make sure to format numbers as currency (e.g. ₹145,190.03) when appropriate.
- Keep the response direct and helpful.
- Reference details from the query or history if helpful.
- Do not mention technical terms like "MCP tool", "database query", or "JSON response". Speak naturally.
"""
        messages = [{"role": "system", "content": system_instruction}]
        
        # Inject context history excluding the pending final format turn
        messages.extend(memory.get_messages()[:-1])
        
        context_prompt = f"""User query: "{user_query}"
Raw JSON response received from the database server:
{json.dumps(raw_tool_result, indent=2)}

Formulate the final response:"""
        
        messages.append({"role": "user", "content": context_prompt})

        response = ollama.chat(
            model=self.model_name,
            messages=messages,
            options={"temperature": 0.3}
        )
        
        return response["message"]["content"].strip()


class PayDeskClient:
    """Manages secure HTTP JSON-RPC communications with the PayDesk MCP server."""

    def __init__(self, server_url: str, token: str, verify_ssl: bool = False, timeout: int = 30) -> None:
        self.server_url = server_url
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def initialize_session(self) -> None:
        """
        Sends an initialize request to the MCP server to obtain an active session ID.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "paydesk-demo-client",
                    "version": "1.0"
                }
            },
            "id": self._next_id()
        }
        
        try:
            response = requests.post(
                self.server_url,
                json=payload,
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            
            if response.status_code == 401:
                raise PermissionError("401 Unauthorized: Invalid or missing JWT token.")
            elif response.status_code == 403:
                raise PermissionError("403 Forbidden: Caller lacks required scopes.")
            elif response.status_code != 200:
                raise RuntimeError(f"Server returned HTTP status code {response.status_code}: {response.text}")
                
            self.session_id = response.headers.get("mcp-session-id")
            if not self.session_id:
                raise RuntimeError("Initialize succeeded but did not return 'mcp-session-id' header.")
                
            self.headers["mcp-session-id"] = self.session_id
            
        except requests.exceptions.Timeout:
            raise TimeoutError("Timeout occurred while establishing session with PayDesk MCP server.")
        except requests.exceptions.ConnectionError:
            raise ConnectionError("Could not connect to the PayDesk MCP server. Ensure it is running and accessible.")
    def _parse_mcp_response(self, response: requests.Response) -> dict:
        """
        Parse both JSON and text/event-stream responses from FastMCP.
        """

        content_type = response.headers.get("content-type", "")

        # Normal JSON response
        if "application/json" in content_type:
            return response.json()

        # FastMCP SSE response
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())

            raise RuntimeError("No JSON payload found in SSE response.")

        raise RuntimeError(f"Unsupported content type: {content_type}")
    def list_tools(self) -> List[Dict[str, Any]]:
        """
        Fetches the list of registered tools from the MCP server.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": self._next_id()
        }
        
        try:
            response = requests.post(
                self.server_url,
                json=payload,
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            
            if response.status_code != 200:
                raise RuntimeError(f"Failed to list tools. HTTP {response.status_code}: {response.text}")
                
            print("=" * 80)
            print("STATUS:", response.status_code)
            print("HEADERS:", response.headers)
            print("RAW RESPONSE:")
            print(response.text)
            print("=" * 80)

            resp_json = self._parse_mcp_response(response)
            if "error" in resp_json:
                raise RuntimeError(f"JSON-RPC Error listing tools: {resp_json['error']}")
                
            result = resp_json.get("result", {})
            return result.get("tools", [])
            
        except Exception as e:
            raise RuntimeError(f"Error during tools discovery: {str(e)}")

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], requests.Response]:
        """
        Executes a tool call using JSON-RPC and returns the request payload and response object.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            },
            "id": self._next_id()
        }
        
        response = requests.post(
            self.server_url,
            json=payload,
            headers=self.headers,
            verify=self.verify_ssl,
            timeout=self.timeout
        )
        return payload, response


# =====================================================================
# Main Execution Loop
# =====================================================================

def main() -> None:
    logger = Logger()
    
    # Initialize connection
    client = PayDeskClient(
        server_url=SERVER_URL,
        token=JWT_TOKEN,
        verify_ssl=VERIFY_SSL,
        timeout=TIMEOUT
    )
    
    logger.print_header(SERVER_URL, "MER-1005", OLLAMA_MODEL)
    
    logger.console.print("[cyan]Establishing secure session with MCP server...[/cyan]")
    try:
        client.initialize_session()
        logger.console.print("[green]Session established successfully.[/green]")
    except Exception as e:
        logger.log_error(f"Failed to initialize session: {str(e)}")
        logger.log_error("Please make sure the MCP server is running on https://127.0.0.1:8000")
        sys.exit(1)
        
    logger.console.print("[cyan]Discovering active database tools...[/cyan]")
    try:
        tools = client.list_tools()
        logger.console.print(f"[green]Discovered {len(tools)} database tools.[/green]\n")
    except Exception as e:
        logger.log_error(f"Failed to discover tools: {str(e)}")
        sys.exit(1)
        
    planner = LLMPlanner(model_name=OLLAMA_MODEL)
    memory = ConversationMemory()
    
    while True:
        try:
            user_input = logger.console.input("[bold green]You > [/bold green]").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                logger.console.print("[yellow]Exiting PayDesk AI Assistant. Goodbye![/yellow]")
                break
            
            # --- STEP 1: User Input ---
            logger.log_step(1, "User Input", user_input)
            memory.add_user_message(user_input)

            # --- STEP 2: Sending to LLM... ---
            logger.log_step(2, "Sending to LLM...", "Submitting request and context history to Ollama model...")

            # --- STEP 3: LLM Response ---
            try:
                llm_response_raw = planner.plan_tool_call(memory, tools)
                logger.log_step(3, "LLM Response", llm_response_raw, is_json=True)
                
                tool_plan = json.loads(llm_response_raw)
            except json.JSONDecodeError as e:
                logger.log_error(f"JSON parsing failure: LLM generated invalid JSON structure. {str(e)}")
                memory.pop_last()
                continue
            except Exception as e:
                logger.log_error(f"LLM failure occurred: {str(e)}")
                memory.pop_last()
                continue

            tool_name = tool_plan.get("tool")
            tool_args = tool_plan.get("arguments", {})

            # Check if the LLM decided no tool was required
            if not tool_name:
                logger.log_step(4, "Decision", "No tool call decided by LLM.")
                final_response = "I am not able to perform that operation as there is no corresponding database tool."
                logger.log_step(7, "Assistant", final_response)
                memory.add_assistant_message(final_response)
                continue

            # Validate tool name exists in discovered list
            tool_exists = any(t["name"] == tool_name for t in tools)
            if not tool_exists:
                logger.log_error(f"Invalid tool: The planned tool '{tool_name}' does not exist on the MCP server.")
                memory.pop_last()
                continue

            # --- STEP 4: Preparing HTTPS request ---
            payload, response = None, None
            try:
                # Partially redact authorization header in logs for privacy
                redacted_headers = dict(client.headers)
                auth_val = redacted_headers.get("Authorization", "")
                if len(auth_val) > 20:
                    redacted_headers["Authorization"] = f"{auth_val[:15]}...xxxx"
                
                req_details = f"POST {client.server_url}\n\nHeaders:\n"
                for k, v in redacted_headers.items():
                    req_details += f"  {k}: {v}\n"
                
                logger.log_step(4, "Preparing HTTPS request", req_details)
                
                # Fetch request payload and call tool
                payload, response = client.call_tool(tool_name, tool_args)
                logger.console.print("[bold yellow]Request Body:[/bold yellow]")
                logger.console.print(Syntax(json.dumps(payload, indent=4), "json", theme="monokai", background_color="default"))
                logger.console.print()
                
            except requests.exceptions.Timeout:
                logger.log_error("Timeout error: Request to MCP server timed out.")
                memory.pop_last()
                continue
            except requests.exceptions.ConnectionError:
                logger.log_error("Connection error: MCP server is unavailable.")
                memory.pop_last()
                continue
            except Exception as e:
                logger.log_error(f"Network / request preparation failure: {str(e)}")
                memory.pop_last()
                continue

            # --- STEP 5: Server Response ---
            try:
                # Log specific HTTP response errors
                if response.status_code == 401:
                    logger.log_error("401 Unauthorized: Invalid or expired Bearer Token.")
                    continue
                elif response.status_code == 403:
                    logger.log_error("403 Forbidden: Authorized client lacks scopes for this tool.")
                    continue
                elif response.status_code == 404:
                    logger.log_error("404 Not Found: MCP server endpoint not found.")
                    continue
                elif response.status_code != 200:
                    logger.log_error(f"HTTP Error {response.status_code}: {response.text}")
                    continue

                response_json = client._parse_mcp_response(response)
                logger.log_step(5, "Server Response", response_json, is_json=True)
                
                if "error" in response_json:
                    err = response_json["error"]
                    logger.log_error(f"JSON-RPC Server Error: {err.get('message', err)}")
                    continue
                    
            except json.JSONDecodeError:
                logger.log_error("JSON parsing failure: Server response was not valid JSON.")
                continue
            except Exception as e:
                logger.log_error(f"Error handling server response: {str(e)}")
                continue

            # --- STEP 6: Formatting answer with LLM ---
            logger.log_step(6, "Formatting answer with LLM", "Sending raw JSON back to LLM to formulate natural language reply...")
            try:
                tool_result = response_json.get("result", {})
                final_response = planner.format_final_response(memory, user_input, tool_result)
            except Exception as e:
                logger.log_error(f"LLM final response formatting failure: {str(e)}")
                continue

            # --- STEP 7: Assistant ---
            logger.log_step(7, "Assistant", f"[bold green]{final_response}[/bold green]")
            memory.add_assistant_message(final_response)

        except KeyboardInterrupt:
            logger.console.print("\n[yellow]KeyboardInterrupt detected. Exiting PayDesk AI Assistant.[/yellow]")
            break
        except Exception as e:
            logger.log_error(f"Unexpected application error: {str(e)}")


if __name__ == "__main__":
    main()
