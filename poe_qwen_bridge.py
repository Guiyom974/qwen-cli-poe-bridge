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
from typing import List, Dict, Optional, AsyncGenerator

# --- CONFIGURATION ---
DEFAULT_POE_MODEL = "Qwen-3-235B-0527-T"
POE_API_KEY = os.environ.get("POE_CALLER_API_KEY1")
MODAL_AUTH_TOKEN = os.environ.get("MODAL_AUTH_TOKEN")

# --- OPENAI-COMPATIBLE DATA MODELS ---

# For Non-Streaming Responses
class OpenAIMessage(BaseModel):
    role: str
    content: str

class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: OpenAIMessage
    finish_reason: str = "stop"

class OpenAIChatResponse(BaseModel):
    id: str = Field(default_factory=lambda: "chatcmpl-" + os.urandom(12).hex())
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChatCompletionChoice]

# For Streaming Responses
class DeltaMessage(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None

class StreamingChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Optional[str] = None

class OpenAIStreamingResponse(BaseModel):
    id: str = Field(default_factory=lambda: "chatcmpl-" + os.urandom(12).hex())
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[StreamingChoice]

# --- Main Request Body ---
class OpenAIChatRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    stream: Optional[bool] = False

# --- STREAMING LOGIC ---
async def stream_poe_to_openai_format(
    poe_model: str, user_prompt: str
) -> AsyncGenerator[str, None]:
    """
    This generator function streams the response from Poe and formats each chunk
    into the OpenAI Server-Sent Event (SSE) format.
    """
    # First, send a chunk to establish the role
    delta_role = DeltaMessage(role="assistant", content="")
    choice_role = StreamingChoice(delta=delta_role)
    stream_chunk_role = OpenAIStreamingResponse(model=poe_model, choices=[choice_role])
    yield f"data: {stream_chunk_role.model_dump_json()}\n\n"

    # Stream the actual content from Poe
    poe_messages = [fp.ProtocolMessage(role="user", content=user_prompt)]
    full_response_text = ""
    async for partial in fp.get_bot_response(
        messages=poe_messages, bot_name=poe_model, api_key=POE_API_KEY
    ):
        # According to the docs, partial.text contains the *next token*, not the full text.
        if partial.text:
            # CORRECTED LINE: Use partial.text instead of partial.text_new
            delta = DeltaMessage(content=partial.text)
            choice = StreamingChoice(delta=delta)
            stream_chunk = OpenAIStreamingResponse(model=poe_model, choices=[choice])
            yield f"data: {stream_chunk.model_dump_json()}\n\n"
    
    # Send the final termination chunk
    delta_stop = DeltaMessage()
    choice_stop = StreamingChoice(delta=delta_stop, finish_reason="stop")
    stream_chunk_stop = OpenAIStreamingResponse(model=poe_model, choices=[choice_stop])
    yield f"data: {stream_chunk_stop.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"

# --- FASTAPI APP ---
app = FastAPI(title="Poe to OpenAI-Format Bridge")

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

    # (Prompt and model selection logic remains the same)
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")
    user_prompt = request.messages[-1].content
    selected_model = DEFAULT_POE_MODEL
    model_match = re.match(r"^\s*#@([\w.-]+)\s*", user_prompt)
    if model_match:
        selected_model = model_match.group(1)
        user_prompt = user_prompt[model_match.end():]

    # --- UPDATED: Handle Streaming vs. Non-Streaming ---
    if request.stream:
        # If the client requests a stream, return a StreamingResponse.
        return StreamingResponse(
            stream_poe_to_openai_format(selected_model, user_prompt),
            media_type="text/event-stream",
        )
    else:
        # Otherwise, use the original non-streaming logic.
        try:
            poe_messages = [fp.ProtocolMessage(role="user", content=user_prompt)]
            final_text = ""
            async for partial in fp.get_bot_response(messages=poe_messages, bot_name=selected_model, api_key=POE_API_KEY):
                # For non-streaming, we must concatenate the chunks.
                final_text += partial.text
            
            response_message = OpenAIMessage(role="assistant", content=final_text)
            choice = ChatCompletionChoice(message=response_message)
            return OpenAIChatResponse(model=selected_model, choices=[choice])
        except Exception as e:
            error_message = f"Error from Poe API: {str(e)}"
            response_message = OpenAIMessage(role="assistant", content=error_message)
            choice = ChatCompletionChoice(message=response_message)
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