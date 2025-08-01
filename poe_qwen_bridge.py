# poe_qwen_bridge.py
import modal
import os
import re
import time
import json
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse
import fastapi_poe as fp
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any

# --- CONFIGURATION ---
DEFAULT_POE_MODEL = "Qwen-3-235B-0527-T"
POE_API_KEY = os.environ.get("POE_CALLER_API_KEY1")
MODAL_AUTH_TOKEN = os.environ.get("MODAL_AUTH_TOKEN")

# --- NEW: THE MASTER AGENT SYSTEM PROMPT ---
# This prompt gives the Poe bot its core identity and instructions.
AGENT_SYSTEM_PROMPT = """
You are an expert AI pair programmer acting as a command-line agent within a user's development environment (like VS Code's terminal). Your primary goal is to help the user with their code by reading, writing, and editing files, and running commands.

**Golden Rule: How to Use Tools**
When you decide to use a tool, you MUST respond with ONLY a valid JSON object containing a "tool_calls" list. Do not add any other text, explanations, or markdown formatting around the JSON.

Correct Tool Use Example:
{"tool_calls": [{"id": "call_abc123", "type": "function", "function": {"name": "edit_file", "arguments": "{\\"file_path\\": \\"src/main.py\\", \\"content\\": \\"print('Hello, World!')\\"}"}}]}

**Behavioral Guidelines:**
1.  **Think Step-by-Step:** Before acting, consider the user's request. If you need to read a file first to understand the context before editing it, plan to call the `read_file` tool first.
2.  **Ask for Clarification:** If a request is ambiguous (e.g., "fix my code"), ask for more information (e.g., "Which file has the bug? Can you describe the error?").
3.  **Standard Chat:** If you are just answering a question, providing an explanation, or writing a code snippet without using a tool, respond in plain Markdown as a standard chatbot. Do NOT use the JSON tool format for this.
"""

# --- OPENAI-COMPATIBLE DATA MODELS ---
# (These are expanded to fully support tool calls)
class OpenAIMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None

class OpenAIChatRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    tools: Optional[List[Dict]] = None
    tool_choice: Optional[Any] = None
    stream: Optional[bool] = False

class FunctionCall(BaseModel):
    name: str
    arguments: str

class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: "call_" + os.urandom(8).hex())
    type: str = "function"
    function: FunctionCall

class AssistantMessage(BaseModel):
    role: str = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None

class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: AssistantMessage
    finish_reason: str

class OpenAIChatResponse(BaseModel):
    id: str = Field(default_factory=lambda: "chatcmpl-" + os.urandom(12).hex())
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChatCompletionChoice]

# --- Tool Formatting Helper ---
def format_tools_for_prompt(tools: Optional[List[Dict]]) -> str:
    """Converts the OpenAI tool list into a simple text manifest for the prompt."""
    if not tools:
        return "No tools are available for this request."
    
    formatted_string = "## Available Tools for This Request\n"
    for tool in tools:
        if tool.get("type") == "function" and "function" in tool:
            func = tool["function"]
            name = func.get("name")
            description = func.get("description")
            parameters = json.dumps(func.get("parameters", {}))
            formatted_string += f"- **{name}**: {description}\n  - Parameters: `{parameters}`\n"
    return formatted_string

# --- FASTAPI APP ---
app = FastAPI(title="Poe to Qwen-Code Agent Bridge")

@app.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest, authorization: Optional[str] = Header(None)):
    # (Authentication logic remains the same)
    if not MODAL_AUTH_TOKEN or not POE_API_KEY:
        raise HTTPException(status_code=500, detail="Server not configured.")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header.")
    token = authorization.split(" ")[1]
    if token != MODAL_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid API Key.")

    # --- Construct the Full Agentic Prompt ---
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")
    
    # Format the conversation history into a simple string
    conversation_history = "\n".join([f"**{msg.role}**: {msg.content or ''}" for msg in request.messages])
    
    # Get the dynamic list of tools for this specific request
    dynamic_tool_list = format_tools_for_prompt(request.tools)
    
    # Combine everything into the final prompt for Poe
    final_prompt_to_poe = (
        f"{AGENT_SYSTEM_PROMPT}\n\n"
        f"{dynamic_tool_list}\n\n"
        f"--- CONVERSATION HISTORY & CURRENT REQUEST ---\n"
        f"{conversation_history}\n\n"
        f"**assistant**:"
    )

    # (Model selection logic remains the same)
    selected_model = DEFAULT_POE_MODEL
    model_match = re.match(r"^\s*#@([\w.-]+)\s*", request.messages[-1].content or "")
    if model_match:
        selected_model = model_match.group(1)

    # --- Call Poe and Parse the Response ---
    try:
        poe_messages = [fp.ProtocolMessage(role="user", content=final_prompt_to_poe)]
        final_text = ""
        # Non-streaming is better for parsing tool calls vs. text
        async for partial in fp.get_bot_response(messages=poe_messages, bot_name=selected_model, api_key=POE_API_KEY):
            final_text += partial.text

        final_text = final_text.strip()
        response_message = None
        finish_reason = "stop"

        # Check if the model wants to call a tool
        if final_text.startswith("{") and final_text.endswith("}"):
            try:
                parsed_json = json.loads(final_text)
                if "tool_calls" in parsed_json:
                    response_message = AssistantMessage(tool_calls=parsed_json["tool_calls"])
                    finish_reason = "tool_calls"
            except json.JSONDecodeError:
                pass  # It wasn't valid JSON, so treat as text

        # If it's not a tool call, treat it as a standard text response
        if response_message is None:
            response_message = AssistantMessage(content=final_text)
            finish_reason = "stop"

        choice = ChatCompletionChoice(message=response_message, finish_reason=finish_reason)
        return OpenAIChatResponse(model=selected_model, choices=[choice])

    except Exception as e:
        # Handle errors gracefully
        error_message = f"Error from Poe API: {str(e)}"
        response_message = AssistantMessage(content=error_message)
        choice = ChatCompletionChoice(message=response_message, finish_reason="stop")
        return OpenAIChatResponse(model=selected_model, choices=[choice])

# --- MODAL APP SETUP ---
app_modal = modal.App("poe-qwen-bridge-agent")

image = (
    modal.Image.debian_slim()
    .pip_install("fastapi", "uvicorn", "fastapi-poe", "pydantic")
)

@app_modal.function(
    image=image,
    secrets=[
        modal.Secret.from_name("poe-api-caller-key-secret1"),
        modal.Secret.from_name("modal-auth-token-secret")
    ]
)
@modal.concurrent(max_inputs=10)
@modal.asgi_app()
def fastapi_app():
    return app