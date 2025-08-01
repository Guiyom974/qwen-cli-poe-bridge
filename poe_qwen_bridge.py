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

# --- OPENAI-COMPATIBLE DATA MODELS ---
# We need to expand these to fully support tool calls.

class OpenAIMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None

class OpenAIChatRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    tools: Optional[List[Dict]] = None  # <-- NEW: To receive tool definitions
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
    finish_reason: str = "tool_calls" # Default to tool_calls if tools are used

class OpenAIChatResponse(BaseModel):
    id: str = Field(default_factory=lambda: "chatcmpl-" + os.urandom(12).hex())
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChatCompletionChoice]

# --- NEW: Tool Handling Logic ---

def format_tools_for_prompt(tools: List[Dict]) -> str:
    """Converts the OpenAI tool list into a text-based format for the Poe prompt."""
    if not tools:
        return ""
    
    formatted_string = "You have access to the following tools. Use them when necessary to answer the user's request.\n\n<TOOLS>\n"
    # The Qwen documentation shows the tools are in a 'function' sub-dict
    for tool in tools:
        if tool.get("type") == "function" and "function" in tool:
            func = tool["function"]
            name = func.get("name")
            description = func.get("description")
            parameters = func.get("parameters")
            
            formatted_string += f"- Tool: `{name}`\n"
            formatted_string += f"  Description: {description}\n"
            formatted_string += f"  Parameters (JSON Schema): {json.dumps(parameters)}\n\n"
            
    formatted_string += "</TOOLS>\n\n"
    formatted_string += "To use a tool, you MUST respond with ONLY a JSON object containing a 'tool_calls' list. Do not add any other text. Example format:\n"
    formatted_string += """
    {"tool_calls": [{"id": "call_abc123", "type": "function", "function": {"name": "tool_name", "arguments": "{\\"arg_name\\": \\"arg_value\\"}"}}]}
    """
    return formatted_string

# --- FASTAPI APP ---
app = FastAPI(title="Poe to OpenAI-Format Bridge with Tool Support")

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

    # --- UPDATED: Construct the full prompt with tool instructions ---
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")
    
    # We combine the conversation history and the latest prompt
    full_conversation_prompt = "\n".join([f"{msg.role}: {msg.content}" for msg in request.messages])

    # Format the tools and create the final instruction prompt
    tool_instructions = format_tools_for_prompt(request.tools)
    final_prompt = f"{tool_instructions}\n\nHere is the conversation history and the user's latest request:\n\n{full_conversation_prompt}\n\nAssistant:"

    # (Model selection logic remains the same)
    selected_model = DEFAULT_POE_MODEL
    model_match = re.match(r"^\s*#@([\w.-]+)\s*", request.messages[-1].content or "")
    if model_match:
        selected_model = model_match.group(1)
        print(f"Poe Model Override: {selected_model}")

    # --- Call Poe and Parse the Response ---
    try:
        poe_messages = [fp.ProtocolMessage(role="user", content=final_prompt)]
        final_text = ""
        async for partial in fp.get_bot_response(messages=poe_messages, bot_name=selected_model, api_key=POE_API_KEY):
            final_text += partial.text

        # --- UPDATED: Check if the response is a tool call or regular text ---
        final_text = final_text.strip()
        response_message = None
        finish_reason = "stop"

        if final_text.startswith("{") and final_text.endswith("}"):
            try:
                # It's likely a JSON object for a tool call
                parsed_json = json.loads(final_text)
                if "tool_calls" in parsed_json:
                    # Success! The model wants to call a tool.
                    response_message = AssistantMessage(tool_calls=parsed_json["tool_calls"])
                    finish_reason = "tool_calls"
            except json.JSONDecodeError:
                # It looked like JSON but wasn't valid, treat as text
                pass
        
        if response_message is None:
            # It's a regular text response
            response_message = AssistantMessage(content=final_text)
            finish_reason = "stop"

        choice = ChatCompletionChoice(message=response_message, finish_reason=finish_reason)
        return OpenAIChatResponse(model=selected_model, choices=[choice])

    except Exception as e:
        error_message = f"Error from Poe API: {str(e)}"
        response_message = AssistantMessage(content=error_message)
        choice = ChatCompletionChoice(message=response_message, finish_reason="stop")
        return OpenAIChatResponse(model=selected_model, choices=[choice])

# --- MODAL APP SETUP ---
app_modal = modal.App("poe-qwen-bridge-openai-format")

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