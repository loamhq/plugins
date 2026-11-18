#!/usr/bin/env python3
"""The Images plugin against a fake OpenAI and a fake Google: no network, no
keys, no Loam. Run from anywhere: python3 tests/test_images.py"""

import base64
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import zlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="images-test-"))
VAULT, SPACES, PRIVATE, SETTINGS = (TMP / n for n in ("vault", "spaces", "private", "settings"))
for d in (VAULT, SPACES, PRIVATE, SETTINGS):
    d.mkdir()


def png(w, h, alpha):
    raw = b"".join(b"\x00" + (b"\x00\x00\x00\x00" if alpha else b"\x00\x00\x00") * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PIXEL = png(2, 2, False)
SEEN = []


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SEEN.append((self.path, dict(self.headers), body))
        if self.path.startswith("/v1/images/"):
            if self.headers.get("Authorization") != "Bearer sk-test":
                return self.reply(401, {"error": {"message": "Incorrect API key provided"}})
            if self.path.endswith("/edits") and b'name="image[]"' not in body:
                return self.reply(400, {"error": {"message": "no image"}})
            n = int(json.loads(body)["n"]) if self.path.endswith("/generations") else 1
            if b"forbidden" in body:
                return self.reply(400, {"error": {"message": "blocked", "code": "moderation_blocked"}})
            return self.reply(200, {"data": [{"b64_json": base64.b64encode(PIXEL).decode()}] * n,
                                    "usage": {"input_tokens_details": {"text_tokens": 10},
                                              "output_tokens": 1000}})
        if ":generateContent" in self.path:
            if self.headers.get("x-goog-api-key") != "AIza-test":
                return self.reply(400, {"error": {"message": "API key not valid"}})
            return self.reply(200, {"candidates": [{"content": {"parts": [
                {"text": "here"}, {"inlineData": {"mimeType": "image/png",
                                                  "data": base64.b64encode(PIXEL).decode()}}]},
                "finishReason": "STOP"}]})
        self.reply(404, {})

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
os.environ.update({"LOAM_OPENAI_URL": f"{BASE}/v1", "LOAM_GEMINI_URL": BASE,
                   "LOAM_VAULT": str(VAULT), "LOAM_WORKSPACES": str(SPACES),
                   "LOAM_PLUGIN_PRIVATE": str(PRIVATE), "LOAM_PLUGIN_SETTINGS_DIR": str(SETTINGS),
                   "LOAM_PLUGIN_KEYS": str(PRIVATE / "keys.env")})
sys.path.insert(0, str(HERE / "tools"))
import images_core as core  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:66}{'  | ' + str(detail)[:240] if detail != '' else ''}")


def keys(text):
    (PRIVATE / "keys.env").write_text(text)


keys("")
out = core.generate("a lighthouse")
check("no key: refused, saying where a key goes",
      not out["ok"] and out["error"] == "no_key" and "Accounts" in out["detail"], out)

keys("# openai: saved\nOPENAI_API_KEY=sk-test\nGEMINI_API_KEY=AIza-test\n")
out = core.generate("A lighthouse at night!", quality="low")
today = f"{datetime.now():%Y-%m-%d}"
want = VAULT / "Images" / f"{today}-a-lighthouse-at-night.png"
check("both keys: OpenAI makes it, in the vault's Images folder, by date and words",
      out["ok"] and out["backend"] == "openai" and Path(out["path"]) == want
      and want.read_bytes() == PIXEL and out["vault_paths"] == [f"Images/{want.name}"], out)
check("its cost is reckoned from the usage", out["cost_usd"] == round(10 * 5e-6 + 1000 * 30e-6, 6))
check("the answer says how to show it in a reply", "![" in out["show"] and str(want) in out["show"])
again = core.generate("A lighthouse at night!")
check("nothing is overwritten: the next one is -2", again["ok"] and again["path"].endswith("-2.png"))

(SETTINGS / "values.json").write_text(json.dumps({"backend": "google", "folder": "Art/New"}))
out = core.generate("a fox", size="1536x1024")
sent = json.loads(SEEN[-1][2])
check("the backend setting picks Google; the folder setting is used",
      out["ok"] and out["backend"] == "google" and "/Art/New/" in out["path"], out)
check("Google is asked for the nearest aspect ratio and a 2K image on a 3 model",
      sent["generationConfig"]["imageConfig"] == {"aspectRatio": "3:2", "imageSize": "2K"}, sent)
out = core.generate("a fox", target_path="Art/fox.jpg")
check("a .jpg asked of Google, which answers PNG, is saved as what it is",
      out["ok"] and out["path"].endswith("Art/fox.png"), out)
(SETTINGS / "values.json").write_text("{}")

keys("GEMINI_API_KEY=AIza-test\n")
out = core.generate("a fox")
check("only a Google key: auto uses Google", out["ok"] and out["backend"] == "google", out)
out = core.generate("a fox", backend="openai")
check("a named backend without its key is refused",
      not out["ok"] and out["error"] == "no_key", out)

keys("OPENAI_API_KEY=sk-test\n")
src = SPACES / "iris" / "cover.png"
src.parent.mkdir()
src.write_bytes(png(4, 4, False))
out = core.generate("make it night", source_images=[str(src)], target_path=str(VAULT / "night.webp"))
path, headers, body = SEEN[-1]
check("a source image goes to OpenAI's edits as multipart, with its real type",
      out["ok"] and out["mode"] == "edit" and path.endswith("/edits")
      and b"Content-Type: image/png" in body and b'name="output_format"\r\n\r\nwebp' in body, out)
outside = TMP / "elsewhere.png"
outside.write_bytes(PIXEL)
out = core.generate("x", source_images=[str(outside)])
check("a source outside the vault and the workspaces is refused",
      not out["ok"] and out["error"] == "boundary", out)
mask = VAULT / "mask.png"
mask.write_bytes(png(4, 4, False))
out = core.generate("x", source_images=[str(src)], mask=str(mask))
check("a mask with no transparency is refused, saying why",
      not out["ok"] and "transparen" in out["detail"], out)
mask.write_bytes(png(3, 4, True))
out = core.generate("x", source_images=[str(src)], mask=str(mask))
check("a mask of another size is refused", not out["ok"] and "must match" in out["detail"], out)
mask.write_bytes(png(4, 4, True))
out = core.generate("x", source_images=[str(src)], mask=str(mask))
check("a right mask is sent", out["ok"] and b'name="mask"' in SEEN[-1][2], out)
for size, word in (("1000x1000", "multiples"), ("4000x1024", "3840"), ("3840x1024", "3:1")):
    out = core.generate("x", size=size)
    check(f"size {size} is refused ({word})", not out["ok"] and word in out["detail"], out)
out = core.generate("forbidden thing")
check("OpenAI's refusal is passed on in its own words",
      not out["ok"] and out["error"] == "content_policy" and "blocked" in out["detail"], out)
out = core.generate("x", target_path="a?b.png")
check("a name a phone cannot store is refused", not out["ok"] and "phone" in out["detail"], out)
log = [json.loads(l) for l in (PRIVATE / "images.jsonl").read_text().splitlines()]
check("every call is a line in the plugin's private log, with no key in it",
      len(log) >= 15 and "sk-test" not in (PRIVATE / "images.jsonl").read_text())

# The MCP server, as an engine starts it.
proc = subprocess.run([sys.executable, str(HERE / "tools" / "images_mcp.py")], input="\n".join(json.dumps(m) for m in [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "generate_image", "arguments": {"prompt": "a small boat", "quality": "low",
                                                         "unknown": 1}}}]) + "\n",
    capture_output=True, text=True, timeout=60, env=os.environ)
answers = [json.loads(l) for l in proc.stdout.splitlines()]
listed = answers[1]["result"]["tools"]
called = json.loads(answers[2]["result"]["content"][0]["text"])
check("MCP: one tool, generate_image", [t["name"] for t in listed] == ["generate_image"], listed)
check("MCP: a call makes the image and answers its path",
      called["ok"] and Path(called["path"]).is_file() and not answers[2]["result"].get("isError"),
      called)

srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
