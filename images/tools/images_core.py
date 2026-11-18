"""Images: make or change a picture with OpenAI or Google, and save it in the
vault as an ordinary file (PNG, JPEG or WebP).

No dependencies beyond Python's standard library, so the plugin installs
anywhere Loam does. Everything it needs from Loam comes in its environment:

- ``LOAM_PLUGIN_KEYS``: a file of ``NAME=value`` lines Loam writes with the
  keys this plugin declared (``OPENAI_API_KEY``, ``GEMINI_API_KEY``), from
  Settings › Accounts. ``$LOAM_PLUGIN_PRIVATE/keys.env`` when not given.
- ``LOAM_PLUGIN_SETTINGS_DIR``: its settings' ``values.json`` (folder,
  backend, models).
- ``LOAM_VAULT``, ``LOAM_WORKSPACES``: where a relative path lands, and the
  places a source image may be read from.
- ``LOAM_PLUGIN_PRIVATE``: where the call log goes (``images.jsonl``).

The OpenAI half is the composition server's ``generate_image`` as it was on
the first Loam (gpt-image-2, verified against the live API 2026-09-02),
without httpx and Pillow. The Google half is Gemini's image models over
their REST API.
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

# -- configuration -------------------------------------------------------------

DEFAULTS = {"folder": "Images", "backend": "auto",
            "openai_model": "gpt-image-2", "gemini_model": "gemini-3-pro-image-preview"}

OPENAI_BASE = (os.environ.get("LOAM_OPENAI_URL") or os.environ.get("OPENAI_API_BASE")
               or "https://api.openai.com/v1").rstrip("/")
GEMINI_BASE = (os.environ.get("LOAM_GEMINI_URL")
               or "https://generativelanguage.googleapis.com").rstrip("/")

# gpt-image-2, as probed 2026-09-02: quality low|medium|high|auto; output_format
# png|webp|jpeg; background transparent|opaque|auto; moderation auto|low;
# size WxH, both edges multiples of 16, longest at most 3840, at most 3:1.
QUALITIES = ("low", "medium", "high", "auto")
BACKGROUNDS = ("transparent", "opaque", "auto")
MODERATIONS = ("auto", "low")
DEFAULT_SIZE, DEFAULT_QUALITY = "1024x1024", "high"
MAX_N, MAX_SOURCES = 4, 8
SIZE_MULTIPLE, SIZE_MAX_EDGE, SIZE_MAX_RATIO = 16, 3840, 3.0
SIZE_RE = re.compile(r"^\s*(\d{1,5})\s*[xX×]\s*(\d{1,5})\s*$")
# Gemini takes an aspect ratio, not pixels: the nearest of these.
ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
FORMATS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}
MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
EXT_OF = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
# $ per token, OpenAI's page 2026-09-02: text in, image in, cached, out.
PRICE = {"text": 5.00e-6, "image": 8.00e-6, "cached": 2.00e-6, "out": 30.00e-6}
TIMEOUT_S = 600
# A file with one of these never reaches an Android phone.
ANDROID_ILLEGAL = set('?:*"<>|')


class Refused(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind, self.detail = kind, detail


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v) if v else None


def read_env(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8") if path else ""
    except OSError:
        return out
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() and not k.strip().startswith("#"):
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def keys() -> dict[str, str]:
    path = _env_path("LOAM_PLUGIN_KEYS")
    if path is None and os.environ.get("LOAM_PLUGIN_PRIVATE"):
        path = Path(os.environ["LOAM_PLUGIN_PRIVATE"]) / "keys.env"
    return {k: v for k, v in read_env(path).items() if v}


def settings() -> dict[str, str]:
    out = dict(DEFAULTS)
    folder = _env_path("LOAM_PLUGIN_SETTINGS_DIR")
    try:
        stored = json.loads((folder / "values.json").read_text(encoding="utf-8")) if folder else {}
    except (OSError, ValueError):
        stored = {}
    for k in DEFAULTS:
        v = stored.get(k) if isinstance(stored, dict) else None
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def vault() -> Path:
    return Path(os.environ.get("LOAM_VAULT") or "/nonexistent").resolve()


def readable_roots() -> list[Path]:
    roots = [vault()]
    if os.environ.get("LOAM_WORKSPACES"):
        roots.append(Path(os.environ["LOAM_WORKSPACES"]).resolve())
    return roots


def choose_backend(have: dict[str, str], wanted: str = "auto") -> str:
    """OpenAI when its key is there, else Google; a named one must have its key."""
    wanted = (wanted or "auto").lower()
    if wanted in ("google", "gemini"):
        wanted = "google"
    if wanted not in ("auto", "openai", "google"):
        raise Refused("validation", f"backend is auto, openai or google, not {wanted!r}")
    need = {"openai": "OPENAI_API_KEY", "google": "GEMINI_API_KEY"}
    if wanted != "auto":
        if not have.get(need[wanted]):
            raise Refused("no_key", f"there is no {'OpenAI' if wanted == 'openai' else 'Google'} "
                                    f"key in Settings › Accounts for Images")
        return wanted
    for b in ("openai", "google"):
        if have.get(need[b]):
            return b
    raise Refused("no_key", "Images has no key to use: add an OpenAI or a Google key in "
                            "Settings › Accounts (the owner, or you with keys.set when "
                            "they give you one)")


# -- checks ----------------------------------------------------------------------

def parse_size(size: str) -> tuple[int, int]:
    m = SIZE_RE.match(size or "")
    if not m:
        raise Refused("validation", f"size is WIDTHxHEIGHT, like '1536x1024'; got {size!r}")
    w, h = int(m.group(1)), int(m.group(2))
    if not w or not h:
        raise Refused("validation", f"size {size!r} has a zero edge")
    if w % SIZE_MULTIPLE or h % SIZE_MULTIPLE:
        near = lambda x: max(SIZE_MULTIPLE, round(x / SIZE_MULTIPLE) * SIZE_MULTIPLE)  # noqa: E731
        raise Refused("validation", f"size {size!r}: both edges are multiples of "
                                    f"{SIZE_MULTIPLE}; the nearest is {near(w)}x{near(h)}")
    if max(w, h) > SIZE_MAX_EDGE:
        raise Refused("validation", f"size {size!r}: the longest edge is at most {SIZE_MAX_EDGE}")
    if max(w, h) / min(w, h) > SIZE_MAX_RATIO + 1e-9:
        raise Refused("validation", f"size {size!r} is wider than 3:1")
    return w, h


def nearest_aspect(w: int, h: int) -> str:
    return min(ASPECTS, key=lambda a: abs(int(a.split(":")[0]) / int(a.split(":")[1]) - w / h))


def png_info(path: Path) -> tuple[int, int, bool] | None:
    """(width, height, has alpha) of a PNG, from its header; None if not one."""
    try:
        with path.open("rb") as fh:
            head = fh.read(33)
            if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
                return None
            w, h = struct.unpack(">II", head[16:24])
            colour = head[25]
            alpha = colour in (4, 6)
            if not alpha:
                rest = fh.read(1 << 20)
                alpha = b"tRNS" in rest.split(b"IDAT", 1)[0]
            return w, h, alpha
    except OSError:
        return None


def _within(path: Path, roots: list[Path]) -> bool:
    return any(path == r or path.is_relative_to(r) for r in roots)


def source(raw) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise Refused("validation", "each source image is a path")
    p = Path(raw)
    if not p.is_absolute():
        p = vault() / raw.lstrip("/")
    try:
        p = p.resolve()
    except OSError:
        raise Refused("validation", f"source image {raw!r} cannot be read") from None
    if not _within(p, readable_roots()):
        raise Refused("boundary", f"source image {raw!r} is outside the vault and the workspaces")
    if not p.is_file() or not os.access(p, os.R_OK):
        raise Refused("validation", f"source image {raw!r} does not exist or cannot be read")
    if p.suffix.lower() not in MIMES:
        raise Refused("validation", f"source image {raw!r} is PNG, JPEG or WebP, not {p.suffix or 'none'}")
    return p


def slug(text: str, most: int = 48) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (words[:most].rstrip("-") or "image")


def target(raw: str | None, prompt: str, folder: str, ext: str) -> Path:
    """Where the first image goes: a path given (vault-relative or absolute),
    or `<vault>/<folder>/<date> <words of the prompt>.<ext>`."""
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = vault() / raw.lstrip("/")
        if p.suffix.lower() not in FORMATS:
            raise Refused("validation", f"the path ends in .png, .jpg or .webp; got {p.suffix or 'none'!r}")
    else:
        p = vault() / folder.strip("/") / f"{datetime.now():%Y-%m-%d}-{slug(prompt)}{ext}"
    p = Path(os.path.abspath(p))
    bad = sorted(set(p.name) & ANDROID_ILLEGAL)
    if bad:
        raise Refused("validation", f"the name {p.name!r} has {''.join(bad)!r}, which a phone "
                                    f"cannot store; choose another")
    return p


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        c = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not c.exists():
            return c
    raise Refused("write_error", f"no free name beside {path}")


# -- the two backends -------------------------------------------------------------

def _post(url: str, headers: dict, body: bytes, who: str) -> dict:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={**headers, "User-Agent": "Loam-Images/1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            err = (json.loads(raw).get("error") or {})
        except (ValueError, AttributeError):
            err = {}
        message = (err.get("message") if isinstance(err, dict) else None) or raw[:500].decode("utf-8", "replace")
        code = err.get("code") if isinstance(err, dict) else None
        kind = ("content_policy" if code in ("moderation_blocked", "content_policy_violation")
                else "no_key" if exc.code in (401, 403)
                else "validation" if exc.code == 400 else "api_error")
        raise Refused(kind, f"{who}: {message} (HTTP {exc.code})") from None
    except TimeoutError:
        raise Refused("timeout", f"{who} did not answer within {TIMEOUT_S} s") from None
    except (urllib.error.URLError, OSError) as exc:
        raise Refused("api_error", f"could not reach {who} ({getattr(exc, 'reason', exc)})") from None


def multipart(fields: dict[str, str], files: list[tuple[str, Path, str]]) -> tuple[bytes, str]:
    boundary = f"loam{uuid.uuid4().hex}"
    out = bytearray()
    for k, v in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                f"{v}\r\n").encode()
    for field, path, mime in files:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                f"filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        out += path.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def cost(usage) -> float | None:
    if not isinstance(usage, dict):
        return None
    d = usage.get("input_tokens_details") or {}
    text_in, image_in = int(d.get("text_tokens") or 0), int(d.get("image_tokens") or 0)
    cached, out = int(d.get("cached_tokens") or 0), int(usage.get("output_tokens") or 0)
    cached = cached if 0 < cached <= image_in else 0
    return round(text_in * PRICE["text"] + (image_in - cached) * PRICE["image"]
                 + cached * PRICE["cached"] + out * PRICE["out"], 6)


def openai_images(key: str, model: str, prompt: str, sources: list[Path], mask: Path | None,
                  size: str, quality: str, n: int, fmt: str, background: str,
                  moderation: str | None) -> tuple[list[tuple[bytes, str]], dict | None]:
    common = {"model": model, "prompt": prompt, "size": size, "quality": quality,
              "n": str(n), "output_format": fmt, "background": background}
    if moderation:
        common["moderation"] = moderation
    auth = {"Authorization": f"Bearer {key}"}
    if sources:
        files = [("image[]", s, MIMES[s.suffix.lower()]) for s in sources]
        if mask:
            files.append(("mask", mask, "image/png"))
        body, ctype = multipart(common, files)
        answer = _post(f"{OPENAI_BASE}/images/edits", {**auth, "Content-Type": ctype}, body, "OpenAI")
    else:
        payload = {**common, "n": n}
        answer = _post(f"{OPENAI_BASE}/images/generations",
                       {**auth, "Content-Type": "application/json"},
                       json.dumps(payload).encode(), "OpenAI")
    mime = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}[fmt]
    images = [(base64.b64decode(d["b64_json"]), mime) for d in answer.get("data") or []
              if d.get("b64_json")]
    if not images:
        raise Refused("api_error", "OpenAI answered with no image")
    return images, answer.get("usage")


def gemini_image(key: str, model: str, prompt: str, sources: list[Path],
                 aspect: str) -> tuple[bytes, str]:
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": MIMES[s.suffix.lower()],
                         "data": base64.b64encode(s.read_bytes()).decode()}} for s in sources]
    image_config = {"aspectRatio": aspect}
    if "-3-" in model or model.endswith("-3"):
        image_config["imageSize"] = "2K"
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": image_config}}
    answer = _post(f"{GEMINI_BASE}/v1beta/models/{model}:generateContent",
                   {"x-goog-api-key": key, "Content-Type": "application/json"},
                   json.dumps(body).encode(), "Google")
    block = (answer.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise Refused("content_policy", f"Google blocked the prompt ({block})")
    for cand in answer.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"]), blob.get("mimeType") or blob.get("mime_type") or "image/png"
        reason = cand.get("finishReason")
        if reason and reason not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
            raise Refused("content_policy", f"Google stopped ({reason})")
    raise Refused("api_error", "Google answered with no image")


# -- the call ---------------------------------------------------------------------

def log(record: dict) -> None:
    private = os.environ.get("LOAM_PLUGIN_PRIVATE")
    if not private:
        return
    try:
        path = Path(private) / "images.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def generate(prompt: str, target_path: str | None = None, source_images=None,
             mask: str | None = None, size: str = DEFAULT_SIZE, quality: str = DEFAULT_QUALITY,
             n: int = 1, background: str = "auto", moderation: str | None = None,
             backend: str | None = None, agent: str | None = None) -> dict:
    started = time.monotonic()
    record = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "agent": agent}
    try:
        if not isinstance(prompt, str) or not prompt.strip():
            raise Refused("validation", "prompt is what the image should be, in words")
        conf = settings()
        have = keys()
        chosen = choose_backend(have, backend or conf["backend"])
        w, h = parse_size(size)
        if quality not in QUALITIES:
            raise Refused("validation", f"quality is one of {', '.join(QUALITIES)}")
        if background not in BACKGROUNDS:
            raise Refused("validation", f"background is one of {', '.join(BACKGROUNDS)}")
        if moderation is not None and moderation not in MODERATIONS:
            raise Refused("validation", f"moderation is one of {', '.join(MODERATIONS)}")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_N:
            raise Refused("validation", f"n is 1 to {MAX_N}")
        if isinstance(source_images, str):
            source_images = [source_images]
        sources = [source(s) for s in source_images or []]
        if len(sources) > MAX_SOURCES:
            raise Refused("validation", f"at most {MAX_SOURCES} source images")
        mask_path = None
        if mask:
            if not sources:
                raise Refused("validation", "a mask marks a region of a source image: pass "
                                            "the image in source_images")
            mask_path = source(mask)
            info, first = png_info(mask_path), png_info(sources[0])
            if info is None or mask_path.suffix.lower() != ".png":
                raise Refused("validation", "the mask is a PNG whose transparent pixels mark "
                                            "the region to repaint")
            if not info[2]:
                raise Refused("validation", "the mask has no transparency: its transparent "
                                            "pixels are what mark the region")
            if first and info[:2] != first[:2]:
                raise Refused("validation", f"the mask is {info[0]}x{info[1]} and the first "
                                            f"source {first[0]}x{first[1]}; they must match")
        ext = Path(target_path).suffix.lower() if target_path else ".png"
        if ext not in FORMATS:
            ext = ".png"
        if background == "transparent" and ext in (".jpg", ".jpeg"):
            raise Refused("validation", "a transparent background needs .png or .webp")
        first_target = target(target_path, prompt, conf["folder"], ext)
        if any(s == first_target for s in sources):
            raise Refused("validation", "the result never replaces a source: give it a new name")
        record.update({"backend": chosen, "mode": "edit" if sources else "generate",
                       "quality": quality, "size": size, "n": n,
                       "sources": [str(s) for s in sources]})
        usage = None
        if chosen == "openai":
            model = conf["openai_model"]
            images, usage = openai_images(have["OPENAI_API_KEY"], model, prompt, sources,
                                          mask_path, size, quality, n, FORMATS[ext],
                                          background, moderation)
        else:
            model = conf["gemini_model"]
            if mask_path:
                raise Refused("unsupported", "Google's image models take no mask; describe the "
                                             "region in the prompt, or use backend='openai'")
            images = [gemini_image(have["GEMINI_API_KEY"], model, prompt, sources,
                                   nearest_aspect(w, h)) for _ in range(n)]
        first_target.parent.mkdir(parents=True, exist_ok=True)
        written = []
        for data, mime in images:
            # A file's name says what it is: Google may answer PNG for a .jpg.
            want = EXT_OF.get(mime, ext)
            out = unique(first_target if first_target.suffix.lower() == want or
                         (want == ".jpg" and first_target.suffix.lower() == ".jpeg")
                         else first_target.with_suffix(want))
            out.write_bytes(data)
            written.append(out)
        v = vault()
        rel = [str(p.relative_to(v)) if p.is_relative_to(v) else None for p in written]
        spent = cost(usage)
        record.update({"ok": True, "model": model, "paths": [str(p) for p in written],
                       "usage": usage, "cost_usd": spent,
                       "elapsed_s": round(time.monotonic() - started, 2)})
        log(record)
        return {"ok": True, "paths": [str(p) for p in written], "path": str(written[0]),
                "vault_paths": rel, "backend": chosen, "model": model,
                "mode": record["mode"], "size": size, "cost_usd": spent,
                "bytes": [p.stat().st_size for p in written],
                "elapsed_s": record["elapsed_s"],
                "show": "To show it in your reply, put "
                        + " ".join(f"![{slug(prompt, 32).replace('-', ' ')}]({p})" for p in written[:3])
                        + " on a line of its own."}
    except Refused as exc:
        record.update({"ok": False, "error": exc.kind, "detail": exc.detail,
                       "elapsed_s": round(time.monotonic() - started, 2)})
        log(record)
        return {"ok": False, "error": exc.kind, "detail": exc.detail}
