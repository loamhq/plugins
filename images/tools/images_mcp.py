#!/usr/bin/env python3
"""The Images tool for agents: a stdio MCP server with no dependencies
(JSON-RPC 2.0, one message per line). One tool, ``generate_image``, the same
for every engine that speaks MCP. The work is images_core.py's."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import images_core as core  # noqa: E402

PROTOCOL = "2025-06-18"
DESCRIPTION = (
    "Make an image, or change one you have, and save it in the vault as an "
    "ordinary file (PNG, JPEG or WebP). OpenAI or Google makes it, whichever "
    "key the owner has in Settings › Accounts.\n\n"
    "  • From words alone: prompt='a cold blue dawn over flooded rice "
    "terraces, woodblock print'.\n"
    "  • Change a picture: source_images=['<path>'] and say what to change.\n"
    "  • In the style of: a reference in source_images, and the new subject "
    "in the prompt.\n"
    "  • Combine several: pass them all; the prompt says how they relate.\n"
    "  • Repaint a region (OpenAI): add mask, a PNG the size of the first "
    "source whose TRANSPARENT pixels are the region.\n\n"
    "Then Read the file to look at what you got, and call again with it as a "
    "source to revise. Paths are absolute, or relative to the vault. "
    "target_path is optional (default: the Images folder, named by date and "
    "prompt); nothing is ever overwritten. size is WIDTHxHEIGHT (multiples of "
    "16, longest edge at most 3840, at most 3:1); quality 'high' by default, "
    "'medium' or 'low' for rough drafts, which cost far less. "
    "To show an image in your reply in a thread, put ![a few words](its path) "
    "on a line of its own: up to three show in your message.")

SCHEMA = {"type": "object", "properties": {
    "prompt": {"type": "string", "description": "What the image is, or what to change."},
    "target_path": {"type": "string", "description": "Where to save it (.png, .jpg or .webp): "
                    "absolute, or relative to the vault. Default: the Images folder."},
    "source_images": {"type": "array", "items": {"type": "string"},
                      "description": "Images to change, combine or take a style from."},
    "mask": {"type": "string", "description": "OpenAI only: a PNG whose transparent pixels "
             "mark the region of the first source to repaint."},
    "size": {"type": "string", "description": "WIDTHxHEIGHT, default 1024x1024."},
    "quality": {"type": "string", "enum": list(core.QUALITIES)},
    "n": {"type": "integer", "minimum": 1, "maximum": core.MAX_N,
          "description": "How many to make, to choose among."},
    "background": {"type": "string", "enum": list(core.BACKGROUNDS),
                   "description": "OpenAI: 'transparent' for a cut-out (.png or .webp)."},
    "moderation": {"type": "string", "enum": list(core.MODERATIONS)},
    "backend": {"type": "string", "enum": ["auto", "openai", "google"],
                "description": "Which service; auto uses OpenAI when its key is there."}},
    "required": ["prompt"]}

TOOLS = [{"name": "generate_image", "description": DESCRIPTION, "inputSchema": SCHEMA}]
ARGS = set(SCHEMA["properties"])


def handle(msg: dict):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "images", "version": "1.0.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != "generate_image":
            out = {"ok": False, "error": "validation", "detail": f"no tool {params.get('name')!r}"}
        else:
            args = {k: v for k, v in (params.get("arguments") or {}).items() if k in ARGS}
            out = core.generate(**args, agent=os.environ.get("LOAM_AGENT") or None)
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
            **({"isError": True} if not out.get("ok") else {})}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
