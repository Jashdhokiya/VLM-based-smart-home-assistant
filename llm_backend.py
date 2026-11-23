"""
INOT Mark1 — LLM backend abstraction

Supported backends (selected via ``LLM_BACKEND`` env var):
  - ``ollama``  : Local via OpenAI-compatible API (http://localhost:11434/v1)
  - ``groq``    : Cloud (existing default)

If ``LLM_GROQ_FALLBACK=true`` and the local backend fails validation,
the query is retried on Groq.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from config import settings
import metrics


def _make_system_prompt(room_context_text: str, device_ids: List[str]) -> str:
    """Build a compact system prompt for the command LLM."""
    devices_str = ", ".join(device_ids)
    return (
        "You are a smart home IoT controller. "
        "Given a user's natural-language command, decide which devices to change.\n"
        f"Valid device IDs: [{devices_str}].\n"
        "Respond ONLY with a JSON object mapping device IDs to actions.\n"
        "Actions: \"on\", \"off\", \"set:<value>\" (e.g. \"set:50\" for brightness/speed).\n"
        "Example: {\"light1\": \"on\", \"fan1\": \"off\"}\n"
        "If no action is needed, respond with {}.\n"
        "Do NOT include any explanation, markdown, or extra text.\n\n"
        f"{room_context_text}"
    )


# ---------------------------------------------------------------------------
# Ollama backend (OpenAI-compatible)
# ---------------------------------------------------------------------------

def _query_ollama(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
) -> str:
    """
    Query Ollama. Tries native /api/chat first, falls back to OpenAI /v1/chat/completions.
    """
    import httpx  # type: ignore[import-untyped]

    model = model or settings.ollama_model
    base_url = settings.ollama_base_url.rstrip("/")
    if base_url.endswith("/v1"):
        root_url = base_url[:-3]
    else:
        root_url = base_url

    # Attempt 1: Native Ollama /api/chat endpoint (most reliable across all Ollama versions)
    native_url = f"{root_url}/api/chat"
    native_payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": settings.llm_temperature,
            "num_predict": settings.llm_max_tokens,
        },
    }

    try:
        resp = httpx.post(
            native_url,
            json=native_payload,
            timeout=settings.llm_timeout_s,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("message", {}).get("content", "").strip()
    except Exception:
        pass

    # Attempt 2: OpenAI-compatible /v1/chat/completions
    v1_url = f"{root_url}/v1/chat/completions"
    v1_payload = {
        "model": model,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "stream": False,
    }

    try:
        resp = httpx.post(
            v1_url,
            json=v1_payload,
            timeout=settings.llm_timeout_s,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException:
        raise TimeoutError(f"Ollama request timed out after {settings.llm_timeout_s}s")
    except Exception as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e


# ---------------------------------------------------------------------------
# Groq backend
# ---------------------------------------------------------------------------

def _query_groq(messages: List[Dict[str, str]]) -> str:
    """Query Groq cloud LLM."""
    from groq import Groq  # type: ignore[import-untyped]

    api_key = settings.groq_api_key
    if not api_key or api_key == "...":
        raise ValueError("GROQ_API_KEY is not set — cannot use Groq LLM backend")

    client = Groq(api_key=api_key)
    model = settings.groq_command_model or settings.groq_llm_model
    res = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )
    return res.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def query(
    user_command: str,
    room_context_text: str,
    device_ids: List[str],
    backend: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Send a user command to the LLM and return structured info.

    Returns::
        {
            "raw":     "<raw LLM text>",
            "backend": "ollama" | "groq",
            "model":   "<model name>",
            "fallback_used": bool,
        }
    """
    be = (backend or settings.llm_backend).lower()

    system_prompt = _make_system_prompt(room_context_text, device_ids)
    messages = [{"role": "system", "content": system_prompt}]

    # Optionally append conversation history
    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": user_command})

    result: Dict[str, Any] = {
        "raw": "",
        "backend": be,
        "model": "",
        "fallback_used": False,
    }

    if be == "ollama":
        model = settings.ollama_model
        result["model"] = model
        try:
            with metrics.stage("llm", note=f"backend=ollama,model={model}"):
                result["raw"] = _query_ollama(messages, model=model)
        except Exception as primary_err:
            # Try fallback model
            fb_model = settings.ollama_fallback_model
            if fb_model and fb_model != model:
                print(f"[LLM] Primary Ollama model failed ({primary_err}), trying {fb_model}...")
                try:
                    with metrics.stage("llm", note=f"backend=ollama,model={fb_model},fallback"):
                        result["raw"] = _query_ollama(messages, model=fb_model)
                        result["model"] = fb_model
                except Exception:
                    pass

            # If still empty and Groq fallback is enabled
            if not result["raw"] and settings.llm_groq_fallback:
                print(f"[LLM] Ollama failed, falling back to Groq cloud...")
                try:
                    with metrics.stage("llm", note="backend=groq,fallback"):
                        result["raw"] = _query_groq(messages)
                        result["backend"] = "groq"
                        result["model"] = settings.groq_command_model
                        result["fallback_used"] = True
                except Exception as groq_err:
                    raise RuntimeError(
                        f"Both Ollama ({primary_err}) and Groq ({groq_err}) failed"
                    ) from groq_err

            if not result["raw"]:
                raise RuntimeError(f"Ollama LLM failed: {primary_err}") from primary_err

    elif be == "groq":
        result["model"] = settings.groq_command_model
        with metrics.stage("llm", note=f"backend=groq,model={settings.groq_command_model}"):
            result["raw"] = _query_groq(messages)

    else:
        raise ValueError(f"Unknown LLM backend '{be}'. Choose 'ollama' or 'groq'.")

    print(f"[LLM] ({result['backend']}/{result['model']}) response: {result['raw'][:200]}")
    return result

