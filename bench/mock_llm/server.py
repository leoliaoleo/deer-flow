"""Minimal OpenAI-compatible mock LLM for load testing (no API cost).

Env vars:
  MOCK_TTFT_MS        delay before the first token (default 300)
  MOCK_TOKEN_DELAY_MS delay between tokens (default 20)
  MOCK_OUTPUT_TOKENS  number of tokens to emit (default 60)
"""
import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

TTFT_MS = float(os.getenv("MOCK_TTFT_MS", "300"))
TOKEN_DELAY_MS = float(os.getenv("MOCK_TOKEN_DELAY_MS", "20"))
OUTPUT_TOKENS = int(os.getenv("MOCK_OUTPUT_TOKENS", "60"))


def est_tokens(obj) -> int:
    return max(1, len(json.dumps(obj, ensure_ascii=False)) // 4)


def sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def make_chunk(cid, model, delta, finish=None):
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mock-llm", "object": "model", "created": 0, "owned_by": "mock"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "mock-llm")
    stream = bool(body.get("stream"))
    n = min(int(body.get("max_tokens") or OUTPUT_TOKENS), OUTPUT_TOKENS)
    prompt_tokens = est_tokens(body.get("messages", []))
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": n, "total_tokens": prompt_tokens + n}
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    words = ["word%d " % i for i in range(n)]

    await asyncio.sleep(TTFT_MS / 1000)

    if not stream:
        await asyncio.sleep(n * TOKEN_DELAY_MS / 1000)
        return JSONResponse({
            "id": cid,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(words)}, "finish_reason": "stop"}],
            "usage": usage,
        })

    async def gen():
        yield sse(make_chunk(cid, model, {"role": "assistant", "content": ""}))
        for w in words:
            await asyncio.sleep(TOKEN_DELAY_MS / 1000)
            yield sse(make_chunk(cid, model, {"content": w}))
        yield sse(make_chunk(cid, model, {}, finish="stop"))
        if (body.get("stream_options") or {}).get("include_usage"):
            yield sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                       "model": model, "choices": [], "usage": usage})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
