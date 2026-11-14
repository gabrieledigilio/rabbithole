"""A stand-in for Ollama, for tests only: the embedding and chat endpoints the app calls.

    python tests/fake_models.py 11999        then run the app with OLLAMA_URL=http://127.0.0.1:11999

"Embedding" a text returns the stored bge-m3 vector of the article whose title the text contains
(or of Talvisota), so searches behave plausibly without the real model. The chat endpoint streams
a canned answer. Nothing here is used when the app runs for real.

Two switches imitate the ways real endpoints differ (tests/test_llm.py flips them):
STRICT refuses a request field it does not know, THINKS puts a thinking block before the answer.
"""
import json
import os
import struct
import sys
import time

from flask import Flask, Response, jsonify, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rabbithole.db import Session  # noqa: E402

app = Flask(__name__)
STRICT = os.environ.get("FAKE_STRICT") == "1"
THINKS = os.environ.get("FAKE_THINKS") == "1"


def vector_for(text):
    with Session() as s:
        row = s.one("""SELECT v.embedding FROM article a JOIN article_vector v USING (lang, page_id)
                       WHERE LOCATE(LOWER(a.title), LOWER(%s)) > 0 AND CHAR_LENGTH(a.title) > 5
                       ORDER BY CHAR_LENGTH(a.title) DESC LIMIT 1""", (text,), quiet=True)
        row = row or s.one("SELECT embedding FROM article_vector WHERE lang = 'fi' AND page_id = 1256", quiet=True)
    return list(struct.unpack("<1024f", row["embedding"]))


@app.post("/api/embed")
def embed():
    texts = request.get_json()["input"]
    return jsonify({"embeddings": [vector_for(t) for t in texts], "prompt_eval_count": 1})


@app.get("/v1/models")
def models():
    return jsonify({"data": [{"id": "bge-m3"}, {"id": "gemma-test"}]})


@app.post("/v1/chat/completions")
def chat():
    body = request.get_json()
    if STRICT and "reasoning_effort" in body:
        return jsonify({"error": {"message": "Unrecognized request argument supplied: reasoning_effort"}}), 400
    words = ("<think>Let me look at the sources first.</think>\n\n" if THINKS else "") + \
            ("This is a canned test answer from the fake model. The first source says one thing [1]. "
             "The second adds another [2]. " + f"(system prompt: {len(body['messages'][0]['content'])} chars, "
             f"user prompt: {len(body['messages'][1]['content'])} chars)")
    words = words.split(" ")

    def pieces():
        for word in words:
            time.sleep(0.02)
            yield "data: " + json.dumps({"choices": [{"delta": {"content": word + " "}}]}) + "\n\n"
        yield "data: [DONE]\n\n"
    return Response(pieces(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 11999, threaded=True)
