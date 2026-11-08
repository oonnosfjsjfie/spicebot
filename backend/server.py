"""SpiceBot server: serves the website (the web/ folder) and answers chat questions with Gemini.

Uses only Python's standard library, so there is nothing to install.
The Gemini key comes from the GEMINI_API_KEY setting (Render > Environment, or backend/.env on your
computer). It is never sent to the browser and never stored on GitHub.

Run on your computer:  python backend/server.py   then open http://localhost:8000
"""
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = (HERE.parent / "web").resolve()


def load_env():
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_BASE = os.getenv("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")

# Tried in this order, like Neutrix. If one is busy, out of quota or not available, the next is used.
MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash,gemini-3.6-flash,gemini-3.1-flash-lite,gemini-3-flash-preview").split(",") if m.strip()]

SYSTEM = """You are SpiceBot, a friendly, warm expert on culinary spices and herbs from every part of the world,
made in Kerala, India. Talk like a helpful friend: short, clear and kind.

How to answer:
- Use the LIBRARY and BOOK notes you are given as your main source. They are checked. If they disagree with
  your own memory, trust the notes. Cite a book as [Book title, p. N] when you use it. Never invent page numbers.
- Cover what was asked: botany, origin and history, chemistry (compounds and formulas), flavour, cooking
  (Kerala and Indian cooking as well as world cuisines), growing, storage, quality and adulteration, health.
- Health: say how strong the evidence is (traditional use / lab or animal studies / small human trials /
  strong clinical evidence). Never diagnose, never give medicinal doses, and mention real safety cautions.
  For emergencies, tell the person to call 108 or 112 in India.
- Casual chat (greetings, how are you, jokes) is welcome: reply warmly in a line or two, then offer to help
  with spices. For other topics, answer very briefly if harmless, then steer back to spices and herbs.
- If you are not sure, say so. Do not make up facts, studies or statistics.
- Use Markdown: short paragraphs, bullet points, and a small table when comparing. Keep most answers under
  180 words unless the person asks for detail. Do not include URLs; the app adds sources itself.
- Visuals: NEVER draw pictures, diagrams or structures with text characters (no ASCII art, no code blocks
  used as pictures). The app draws real visuals for you. When a visual would help, or when the person asks
  to see something, put tags like these on their own lines at the end of your answer (at most 3):
    [[photo: Piper nigrum]]               a real photo of a spice, herb or plant (common or Latin name)
    [[structure: piperine]]               the chemical structure of a compound (its common name)
    [[chart: Title | Label A=12 %; Label B=30 %]]   a bar chart, only with numbers you are confident of
  Describe what the visual shows in words; do not mention the tags themselves.
- Write chemical formulas as plain text like C17H19NO3. Never use LaTeX or $ math notation.
- Language: {language}"""

# A simple limit per visitor so nobody can use up the key's free quota.
HITS = defaultdict(deque)
LIMIT, WINDOW = 15, 60.0


def allowed(ip):
    now, q = time.time(), HITS[ip]
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= LIMIT:
        return False
    q.append(now)
    return True


class AIError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(detail)
        self.kind = kind


def call_model(model, payload):
    req = urllib.request.Request(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read().decode("utf-8"))
    cands = data.get("candidates") or []
    if not cands:
        raise AIError("empty", json.dumps(data.get("promptFeedback", {}))[:200])
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        raise AIError("empty", cands[0].get("finishReason", ""))
    return text


def ask_gemini(contents, system):
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 4096},
    }
    last = "no model answered"
    quota_hits = 0
    for model in MODELS:
        for attempt in range(2):
            try:
                text = call_model(model, payload)
                print(f"[ok] Answered by {model}", flush=True)
                return text, model
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                last = f"{e.code} {body}"
                if e.code == 429 or "RESOURCE_EXHAUSTED" in body:
                    quota_hits += 1
                    print(f"[quota] {model}", flush=True)
                    break
                if e.code in (500, 502, 503, 504):
                    print(f"[busy] {model} try {attempt + 1}", flush=True)
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if e.code in (400, 401, 403) and ("API key" in body or "API_KEY" in body or "PERMISSION" in body):
                    print(f"[key] {model}: {body}", flush=True)
                    raise AIError("bad_key", body)
                print(f"[skip] {model}: {last}", flush=True)
                break
            except AIError as e:
                last = f"{e.kind} {e}"
                print(f"[empty] {model}: {e}", flush=True)
                break
            except Exception as e:  # timeouts, network
                last = str(e)
                print(f"[error] {model}: {last[:200]}", flush=True)
                break
    raise AIError("quota" if quota_hits and quota_hits == len(MODELS) else "busy", last)


def build_contents(body):
    contents = []
    for t in (body.get("history") or [])[-8:]:
        role = "model" if t.get("role") == "model" else "user"
        text = str(t.get("text", ""))[:1500]
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    question = str(body.get("message", "")).strip()[:1500]
    context = str(body.get("context", "")).strip()[:12000]
    if context:
        question = f"{question}\n\n---\nNotes for SpiceBot (not written by the person):\n{context}"
    contents.append({"role": "user", "parts": [{"text": question}]})
    return contents


CSP_SAFE_TYPES = {".html", ".js", ".json", ".webmanifest", ".png", ".jpg", ".svg", ".ico", ".apk", ".txt", ".css", ".woff", ".woff2"}
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/vnd.android.package-archive", ".apk")
mimetypes.add_type("text/javascript", ".js")


class Handler(BaseHTTPRequestHandler):
    server_version = "SpiceBot/1.0"

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/health"):
            print("%s %s" % (self.command, self.path.split("?")[0]), flush=True)

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        path = self.path.split("?")[0].split("#")[0]
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "ai": bool(API_KEY)})
        if path in ("", "/"):
            path = "/index.html"
        try:
            target = (WEB / path.lstrip("/")).resolve()
            target.relative_to(WEB)  # stay inside web/
        except Exception:
            return self.send_json(404, {"error": "not_found"})
        if not target.is_file() or target.suffix.lower() not in CSP_SAFE_TYPES:
            return self.send_json(404, {"error": "not_found"})
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("json"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if target.suffix in (".html", ".js", ".webmanifest") else "public, max-age=86400")
        if target.suffix == ".apk":
            self.send_header("Content-Disposition", 'attachment; filename="SpiceBot.apk"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/api/chat":
            return self.send_json(404, {"error": "not_found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 40000:
                return self.send_json(413, {"error": "too_large", "message": "That message is too long."})
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(body, dict) or not str(body.get("message", "")).strip():
                raise ValueError
        except Exception:
            return self.send_json(400, {"error": "bad_request", "message": "Send a question as JSON."})
        if not API_KEY:
            return self.send_json(503, {"error": "no_key", "message": "The AI isn't set up yet: GEMINI_API_KEY is missing on the server."})
        ip = (self.headers.get("x-forwarded-for") or self.client_address[0]).split(",")[0].strip()
        if not allowed(ip):
            return self.send_json(429, {"error": "rate_limited", "message": "Too many questions at once. Wait a minute and try again."})
        language = ("Always answer in English only, and write any Malayalam, Tamil or Hindi names in English letters."
                    if body.get("english_only") else
                    "Answer in the language the person writes in (English by default). Malayalam, Tamil and Hindi are welcome.")
        try:
            text, model = ask_gemini(build_contents(body), SYSTEM.format(language=language))
            return self.send_json(200, {"answer": text, "model": model})
        except AIError as e:
            if e.kind == "quota":
                return self.send_json(429, {"error": "quota", "message": "SpiceBot's AI has reached its usage limit for now."})
            if e.kind == "bad_key":
                return self.send_json(503, {"error": "bad_key", "message": "The server's Gemini key isn't working."})
            return self.send_json(503, {"error": "busy", "message": "SpiceBot's AI is busy right now."})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    print(f"SpiceBot running on http://{'localhost' if host == '127.0.0.1' else host}:{port}  (AI {'on' if API_KEY else 'off: no GEMINI_API_KEY'})", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
