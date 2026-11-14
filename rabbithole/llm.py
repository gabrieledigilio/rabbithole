"""The two models around the database: one turns a question into a vector, one writes answers.

Both are optional. Without the embedding model the search falls back to keywords; without a chat
model the page shows the sources instead of an answer.
"""
import json
import re
import struct
import time

import requests

from . import config

_seen = {}          # what was reachable when we last looked: {"embed": (time, ok), "llm": (time, model)}
RECHECK = 20        # seconds


def _fresh(key):
    hit = _seen.get(key)
    return hit[1] if hit and time.time() - hit[0] < RECHECK else None


def embed(texts):
    """A list of texts -> a list of vectors as bytes (the form a VECTOR column takes), or None when
    the embedding model cannot be reached."""
    if _fresh("embed") is False:
        return None
    try:
        # Ollama needs both settings: an embedding must fit in one batch (see the RAG demo's README).
        r = requests.post(f"{config.OLLAMA_URL}/api/embed", timeout=(1.5, 120), json={
            "model": config.EMBED_MODEL, "input": texts, "truncate": True,
            "options": {"num_ctx": config.EMBED_CONTEXT, "num_batch": config.EMBED_CONTEXT}})
        r.raise_for_status()
        vectors = r.json()["embeddings"]
        _seen["embed"] = (time.time(), True)
        return [struct.pack(f"<{len(v)}f", *v) for v in vectors]
    except (requests.RequestException, KeyError, ValueError):
        _seen["embed"] = (time.time(), False)
        return None


def _headers():
    return {"Authorization": f"Bearer {config.LLM_API_KEY}"} if config.LLM_API_KEY else {}


def model():
    """The chat model to use: LLM_MODEL, or the first Gemma the endpoint lists, or its first chat model."""
    if config.LLM_MODEL:
        return config.LLM_MODEL
    cached = _fresh("llm")
    if cached is not None:
        return cached or None
    found = ""
    try:
        r = requests.get(f"{config.LLM_BASE_URL}/models", headers=_headers(), timeout=(1.5, 10))
        r.raise_for_status()
        names = [m.get("id", "") for m in r.json().get("data", [])]
        chat = [n for n in names if not re.search(r"embed|bge|minilm|rerank|whisper", n, re.I)]
        for wanted in (r"gemma-?4", r"gemma-?3", r"gemma"):
            found = next((n for n in chat if re.search(wanted, n, re.I)), "")
            if found:
                break
        found = found or (chat[0] if chat else "")
    except (requests.RequestException, ValueError):
        pass
    _seen["llm"] = (time.time(), found)
    return found or None


def status():
    probe = _fresh("embed")
    if probe is None:
        probe = embed(["ping"]) is not None
    return {"embed": bool(probe), "embed_model": config.EMBED_MODEL, "llm": model()}


# A model that thinks before it answers may send the thinking inside the text. Neither form below
# belongs on the page: <think>...</think> (many models) and <|channel>thought...<channel|> (Gemma 4).
_THINKING = (("<think>", "</think>"), ("<|channel>", "<channel|>"))


def without_thinking(pieces):
    """Pass a stream of text through, minus a thinking block at its start. Works whatever way the
    stream is cut into pieces."""
    held, close = "", None          # held: text not yet decided on; close: the end mark we are waiting for
    pieces = iter(pieces)
    for piece in pieces:
        held += piece
        if close is None:
            text = held.lstrip()
            opened = next((pair for pair in _THINKING if text.startswith(pair[0])), None)
            if opened:
                close = opened[1]
            elif text and not any(pair[0].startswith(text) for pair in _THINKING):
                yield text          # an ordinary answer: nothing to hide
                break
            else:
                continue            # too short to tell yet
        if close in held:
            rest = held.split(close, 1)[1].lstrip()
            held, close = "", None
            if rest:
                yield rest
            break
    else:
        if held.strip() and close is None:
            yield held.lstrip()     # the stream ended while we were still deciding
        return
    yield from pieces


def _stream(body):
    with requests.post(f"{config.LLM_BASE_URL}/chat/completions", headers=_headers(), stream=True, timeout=(3, 300), json=body) as r:
        if r.status_code != 200:
            raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}", response=r)
        for line in r.iter_lines():
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            try:
                piece = json.loads(data)["choices"][0].get("delta", {}).get("content")
            except (ValueError, KeyError, IndexError):
                continue
            if piece:
                yield piece


def chat(system, user, max_tokens=900):
    """Stream the answer as pieces of text. Yields nothing but an '⚠ ...' line when no model answers."""
    name = model()
    if not name:
        yield "⚠ No language model is configured. Start Ollama with a chat model, or set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env."
        return
    body = {"model": name, "stream": True, "temperature": 0.2, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    # The answers are short and come with their sources, so ask a thinking model not to think first.
    # An endpoint that does not know the setting refuses it (400); then ask again without.
    attempts = [dict(body, reasoning_effort="none"), body] if _seen.get("plain") != name else [body]
    for attempt in attempts:
        sent = False
        try:
            for piece in without_thinking(_stream(attempt)):
                sent = True
                yield piece
            return
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (400, 422) and attempt is not attempts[-1] and not sent:
                _seen["plain"] = name
                continue
            yield f"⚠ The language model ({name}) answered {e}"
            return
        except requests.RequestException as e:
            yield f"⚠ The language model could not be reached: {type(e).__name__}"
            return
