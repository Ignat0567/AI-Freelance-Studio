"""
MCP Server for FreelancerStudio.

Exposes FreelancerStudio tools to OpenCode via the Model Context Protocol.
This allows OpenCode agents to query project specs, agent status, and business
context directly from FreelancerStudio's backend.

Usage:
    opencode serve --port 4096  # start OpenCode server

Then in opencode.json:
    "mcp": {
        "freelancer-studio": {
            "type": "local",
            "command": ["python", "opencode_mcp_server.py"],
            "enabled": true
        }
    }

OpenCode agents can then use tools like:
    - get_project_data(project_id) - get full project context
    - get_agent_status(agent_id) - check what an agent is doing
    - get_sprint_plan(project_id) - get the current sprint plan
    - ask_human(question) - ask the FreelancerStudio user a question
"""
import json
import sys
import os
import traceback
from typing import Any

# MCP protocol messages use JSON-RPC 2.0 over stdio
# Each message is a single line of JSON terminated by \n


def log(msg: str):
    """Log to stderr (visible in OpenCode's MCP server logs)."""
    print(f"[FreelancerStudio MCP] {msg}", file=sys.stderr, flush=True)


def read_request() -> dict | None:
    """Read a single JSON-RPC request from stdin."""
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        log(f"Invalid JSON: {e}")
        return None


def send_response(id: Any, result: Any = None, error: Any = None):
    """Write a JSON-RPC response to stdout."""
    resp = {"jsonrpc": "2.0", "id": id}
    if error:
        resp["error"] = {"code": error.get("code", -1), "message": error.get("message", "Unknown error")}
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def send_event(method: str, params: dict):
    """Send a JSON-RPC notification (event) to the client."""
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


# ── Tool Implementations ───────────────────────────────────────────────────

def _call_freelancer_api(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Call the FreelancerStudio FastAPI backend."""
    import httpx
    port = os.environ.get("FREELANCER_STUDIO_PORT", "8080")
    base = f"http://127.0.0.1:{port}"
    try:
        if method == "GET":
            r = httpx.get(f"{base}{endpoint}", timeout=5)
        else:
            r = httpx.post(f"{base}{endpoint}", json=data, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def tool_get_project_data(project_id: str = None) -> dict:
    """Get current project data and context from FreelancerStudio."""
    if project_id:
        data = _call_freelancer_api(f"/api/projects/{project_id}/detail")
    else:
        data = _call_freelancer_api("/api/projects/active/current")
    return data


def tool_get_agent_status(agent_id: str = None) -> dict:
    """Get the status of a specific agent or all agents."""
    statuses = _call_freelancer_api("/api/agents/status")
    if agent_id:
        return {"agent": agent_id, "status": statuses.get(agent_id, "unknown")}
    return statuses


def tool_get_sprint_plan(project_id: str = None) -> dict:
    """Get the current sprint plan for a project."""
    if not project_id:
        data = _call_freelancer_api("/api/projects/active/current")
        project_id = data.get("project", {}).get("project_id") if isinstance(data, dict) else None
    if project_id:
        return _call_freelancer_api(f"/api/projects/{project_id}/plan")
    return {"error": "No active project found"}


def tool_ask_human(question: str) -> dict:
    """Ask the user a question through FreelancerStudio's agent question system."""
    data = _call_freelancer_api("/api/projects/active/current")
    project_id = data.get("project", {}).get("project_id") if isinstance(data, dict) else None
    if not project_id:
        return {"error": "No active project"}
    result = _call_freelancer_api(
        f"/api/projects/{project_id}/agent-question",
        method="POST",
        data={"question": question, "agent": "opencode"},
    )
    return result


def tool_get_project_files(project_id: str = None) -> dict:
    """List generated files in the current project."""
    if not project_id:
        data = _call_freelancer_api("/api/projects/active/current")
        project_id = data.get("project", {}).get("project_id") if isinstance(data, dict) else None
    if project_id:
        return _call_freelancer_api(f"/api/projects/{project_id}/files")
    return {"error": "No active project"}


# ── Server Loop ────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_project_data",
        "description": "Get the current active project's full data (title, description, status, logs, etc.) from FreelancerStudio",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Optional project ID. If omitted, returns the current active project.",
                }
            },
        },
    },
    {
        "name": "get_agent_status",
        "description": "Get the status of FreelancerStudio agents (Alex, Maya, Codex, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent ID (alex, maya, elena, codex, bugcatcher, sentinel, lupa, goldie)",
                }
            },
        },
    },
    {
        "name": "get_sprint_plan",
        "description": "Get the current sprint plan and architecture from the active project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Optional project ID"}
            },
        },
    },
    {
        "name": "get_project_files",
        "description": "Get the file directory listing for the active project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Optional project ID"}
            },
        },
    },
    {
        "name": "ask_human",
        "description": "Ask the FreelancerStudio user a question and wait for an answer (useful when you need clarification)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user",
                }
            },
            "required": ["question"],
        },
    },
]

TOOL_HANDLERS = {
    "get_project_data": tool_get_project_data,
    "get_agent_status": tool_get_agent_status,
    "get_sprint_plan": tool_get_sprint_plan,
    "get_project_files": tool_get_project_files,
    "ask_human": tool_ask_human,
}


def main():
    log("FreelancerStudio MCP server starting...")
    log(f"Backend port: {os.environ.get('FREELANCER_STUDIO_PORT', '8080')}")

    while True:
        req = read_request()
        if req is None:
            break

        msg_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        log(f"Request: {method}")

        if method == "tools/list":
            send_response(msg_id, {"tools": TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            log(f"Tool call: {tool_name}({json.dumps(arguments)})")

            handler = TOOL_HANDLERS.get(tool_name)
            if handler:
                try:
                    result = handler(**arguments)
                    send_response(msg_id, {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
                    })
                except Exception as e:
                    send_response(msg_id, error={
                        "code": -1,
                        "message": f"Error executing {tool_name}: {traceback.format_exc()}",
                    })
            else:
                send_response(msg_id, error={"code": -32601, "message": f"Tool not found: {tool_name}"})

        elif method == "initialize":
            send_response(msg_id, {
                "protocolVersion": "0.1.0",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "freelancer-studio", "version": "1.0.0"},
            })
            log("Initialized")

        elif method == "notifications/initialized":
            send_response(msg_id, {})

        elif method == "shutdown":
            send_response(msg_id, {})
            break

        else:
            log(f"Unknown method: {method}")
            send_response(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})

    log("MCP server shutting down")


if __name__ == "__main__":
    main()
