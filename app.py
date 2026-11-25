"""Opus chatbot server.

The Gemini API key lives only in the server environment (.env file or host
environment variables). It is never written into any page, script, template,
response, or log line, so the browser cannot ever see it.
"""
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------- config
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
if API_KEY.lower() in {"", "your_key_here", "your_gemini_api_key_here"}:
    API_KEY = ""
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
# Google retired these for new keys, so they are never tried
RETIRED = {"gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"}
FALLBACKS = [m.strip() for m in os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-2.5-flash"
).split(",") if m.strip()]


def model_chain():
    chain = []
    for m in [MODEL] + FALLBACKS + ["gemini-3.5-flash", "gemini-3.5-flash-lite"]:
        if m and m not in RETIRED and m not in chain:
            chain.append(m)
    return chain
THINKING = os.getenv("GEMINI_THINKING", "low").strip()
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

MAX_MESSAGES = 24
MAX_CHARS = 2000
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("opus")


def scrub(value) -> str:
    """Make sure the key can never appear in a log line or an error."""
    text = str(value)
    return text.replace(API_KEY, "[hidden]") if API_KEY else text


SYSTEM_PROMPT = """You are Opus, a warm and sharp assistant who can answer any question on any topic.

Writing style rules that you must always follow
Write in plain clear everyday language
Never use any punctuation marks such as commas periods colons semicolons question marks exclamation marks quotation marks brackets parentheses slashes or dashes
Never use markdown symbols such as asterisks hashes underscores or backticks outside of code
Never use bullet points or numbered lists and never use emojis
Put each idea on its own short line and leave a blank line between groups of ideas so the answer is easy to read
Write numbers as plain digits and keep decimals like 3.5 as they are
Be accurate and direct and give the full answer without filler

The only exception is code
If the user asks for code put it inside triple backtick fences and write the code normally there
Everything outside the code still follows the no punctuation rules"""

# ---------------------------------------------------------------- app
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
if os.getenv("TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return True
        q.append(now)
        return False


@app.after_request
def secure(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; media-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- answer cleaning
def _clean_text(t: str) -> str:
    t = t.replace("\r", "").replace("\u2019", "'").replace("\u2018", "'")
    for a, b in (("e.g.", "for example"), ("i.e.", "that is"), ("etc.", "and so on"),
                 ("vs.", "versus"), ("E.g.", "For example"), ("I.e.", "That is")):
        t = t.replace(a, b)
    # headings, quotes, bullets and numbered list markers at line start
    t = re.sub(r"(?m)^[ \t]{0,3}(#{1,6}|>|[-*+\u2022]|\d{1,2}[.)])[ \t]+", "", t)
    # thousands separators 1,000 -> 1000
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)
    # sentence ends become line breaks so the answer stays readable
    t = re.sub(r"[.!?\u061F\u06D4]+[\"')\]]*[ \t]+(?=[A-Z0-9\u0600-\u06FF])", "\n", t)
    t = re.sub(r"(?<=\d)\.(?=\d)", "\x01", t)          # protect decimals
    t = t.replace("_", " ")
    t = re.sub(r"[^\w\s'\-%$+=\x01]", " ", t)           # drop all other punctuation
    t = re.sub(r"(?<!\w)'|'(?!\w)", " ", t)              # apostrophes only inside words
    t = re.sub(r"(?<![\w])-|-(?![\w])", " ", t)          # hyphens only inside words
    t = t.replace("\x01", ".")
    t = re.sub(r"[ \t]+", " ", t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def clean_answer(text: str) -> str:
    """Remove punctuation from prose and leave code blocks untouched."""
    parts = text.split("```")
    out = []
    for i, part in enumerate(parts):
        out.append(part.strip("\n") if i % 2 else _clean_text(part))
    pieces = []
    for i, part in enumerate(out):
        if i % 2:
            pieces.append("```" + part + "\n```")
        elif part:
            pieces.append(part)
    return "\n\n".join(pieces).strip()


# ---------------------------------------------------------------- gemini
class Retryable(Exception):
    pass


def call_gemini(model: str, contents: list, use_thinking: bool) -> str:
    gen_cfg = {"temperature": 0.7, "maxOutputTokens": 4096}
    if use_thinking and THINKING:
        gen_cfg["thinkingConfig"] = {"thinkingLevel": THINKING}
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": gen_cfg,
    }
    r = requests.post(
        ENDPOINT.format(model=model),
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
        json=body,
        timeout=60,
    )
    if r.status_code != 200:
        detail = scrub(r.text[:300]).replace("\n", " ")
        log.warning("gemini %s status %s %s", model, r.status_code, detail)
        if r.status_code == 400 and use_thinking and "thinking" in r.text.lower():
            raise ValueError("retry-without-thinking")
        if r.status_code in (400, 401, 403) and "api key" in r.text.lower():
            raise PermissionError("bad-key")
        raise Retryable(str(r.status_code))
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise LookupError("blocked")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise LookupError("empty")
    return text


def ask_gemini(contents: list) -> str:
    last = None
    for model in model_chain():
        log.info("trying model %s", model)
        use_thinking = model.startswith("gemini-3")
        for _ in range(2):
            try:
                return call_gemini(model, contents, use_thinking)
            except ValueError:
                use_thinking = False
                continue
            except Retryable as e:
                last = e
                break
            except requests.RequestException as e:
                log.warning("network error %s", scrub(e))
                last = e
                break
    raise last or RuntimeError("no model answered")


# ---------------------------------------------------------------- routes
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify(ok=True)


@app.route("/api/chat", methods=["POST"])
def chat():
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return jsonify(error="Request blocked"), 403
    if not API_KEY:
        log.error("GEMINI_API_KEY is missing - create a .env file from .env.example")
        return jsonify(error="Opus is not set up yet. The server owner needs to add the API key."), 503
    if rate_limited(request.remote_addr or "unknown"):
        return jsonify(error="Too many messages. Please wait a moment and try again."), 429

    payload = request.get_json(silent=True) or {}
    raw = payload.get("messages")
    if not isinstance(raw, list) or not raw:
        return jsonify(error="Say something first."), 400

    contents = []
    for m in raw[-MAX_MESSAGES:]:
        if not isinstance(m, dict):
            continue
        role = "user" if m.get("role") == "user" else "model"
        text = str(m.get("content", ""))[:MAX_CHARS].strip()
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    if not contents or contents[-1]["role"] != "user":
        return jsonify(error="Say something first."), 400

    try:
        reply = clean_answer(ask_gemini(contents))
        if not reply:
            raise LookupError("empty")
        return jsonify(reply=reply)
    except PermissionError:
        log.error("Gemini rejected the API key - check GEMINI_API_KEY in .env")
        return jsonify(error="Opus cannot reach its brain right now. The API key needs attention."), 502
    except LookupError:
        return jsonify(error="Opus could not answer that one. Try asking it a different way."), 200
    except Exception as e:  # noqa: BLE001
        log.warning("chat failed: %s", scrub(e))
        return jsonify(error="Opus is having trouble right now. Please try again in a moment."), 502


if __name__ == "__main__":
    if not API_KEY:
        log.warning("GEMINI_API_KEY not found - copy .env.example to .env and add your key")
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=False)