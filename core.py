"""PartForge core: projects, settings, local-LLM client, Token Thrift context budgeting,
web research, parameter engine and the FreeCAD bridge. Standard library only.

Run `python core.py` for the self-check.
"""
import ast
import base64
import concurrent.futures
import copy
import ctypes
import glob
import html
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

APP_NAME = "PartForge"
VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("PARTFORGE_HOME") or Path(os.environ.get("APPDATA", Path.home())) / APP_NAME)
DEFAULT_PROJECTS = Path.home() / "Documents" / "PartForge Projects"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # every helper process runs invisible
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

log = logging.getLogger("partforge")


def setup_logging():
    HOME.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    h = RotatingFileHandler(HOME / "partforge.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def write_atomic(path, text):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def fmt(v):
    """60.0 -> '60', 3.40 -> '3.4'."""
    try:
        return f"{float(v):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


# ----------------------------------------------------------------------------- secrets
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI, same approach as Token Thrift's safeStorage: only this Windows user can decrypt."""
    if os.name != "nt":
        return data  # ponytail: plaintext off Windows, use the OS keychain when shipping mac/linux
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


# ----------------------------------------------------------------------------- settings
PRESETS = {"Ollama (local)": "http://127.0.0.1:11434/v1", "LM Studio (local)": "http://127.0.0.1:1234/v1"}


class Settings(dict):
    DEFAULTS = {"base_url": PRESETS["Ollama (local)"], "model": "", "api_key_enc": "", "context_tokens": 16384,
                "lean_coding": True, "fast_reasoning": True, "auto_fix": True, "web_search": True,
                "freecad_cmd": "", "projects_dir": str(DEFAULT_PROJECTS), "recent": [], "last_project": "",
                "quick_send": "creality-print", "apps_custom": [], "app_paths": {}, "bed": [220, 220, 250],
                "photo_inbox": "", "vision_model": ""}
    PATH = HOME / "settings.json"

    @classmethod
    def load(cls):
        s = cls(copy.deepcopy(cls.DEFAULTS))
        try:
            s.update(json.loads(cls.PATH.read_text("utf-8")))
        except (OSError, ValueError):
            pass
        return s

    def save(self):
        HOME.mkdir(parents=True, exist_ok=True)
        write_atomic(self.PATH, json.dumps(self, indent=1))

    @property
    def api_key(self):
        if not self.get("api_key_enc"):
            return ""
        try:
            return _dpapi(base64.b64decode(self["api_key_enc"]), False).decode()
        except (OSError, ValueError):
            return ""

    @api_key.setter
    def api_key(self, key):
        self["api_key_enc"] = base64.b64encode(_dpapi(key.encode(), True)).decode() if key else ""

    def add_recent(self, folder):
        folder = str(folder)
        self["recent"] = [folder] + [r for r in self["recent"] if r != folder][:7]
        self["last_project"] = folder


def find_freecad(hint=""):
    cands = [hint] if hint else []
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        cands += sorted(glob.glob(os.path.join(root, "FreeCAD*", "bin", "freecadcmd.exe")), reverse=True)
    cands += [shutil.which("freecadcmd") or "", shutil.which("FreeCADCmd") or ""]
    return next((c for c in cands if c and os.path.isfile(c)), "")


def freecad_gui(cmd_path):
    exe = Path(cmd_path).with_name("freecad.exe")
    return str(exe) if exe.is_file() else ""


# ----------------------------------------------------------------------------- Token Thrift
# Ported from Token Thrift (context-window.ts / context-compression.ts): chars/4 estimate,
# newest-first history fitting, and rolling summaries that archive old turns instead of dropping them.
CHARS_PER_TOKEN = 4


def est_tokens(text):
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def fit_history(messages, budget):
    """Keep the newest messages that fit the budget; never return empty if there is a message."""
    kept, used = [], 0
    for m in reversed(messages):
        cost = est_tokens(m["content"])
        if used + cost > budget and kept:
            break
        used += cost
        kept.append(m)
    return kept[::-1]


def split_for_summary(messages, keep_budget):
    """(older messages to summarize, recent messages kept raw)."""
    recent = fit_history(messages, keep_budget)
    return messages[:len(messages) - len(recent)], recent


def load_modules(settings):
    out = []
    for key, fname in (("lean_coding", "lean-coding.md"), ("fast_reasoning", "fast-reasoning.md")):
        if settings.get(key):
            try:
                out.append((HERE / "prompt_modules" / fname).read_text("utf-8").strip())
            except OSError:
                pass
    return "\n\n".join(out)


# ----------------------------------------------------------------------------- LLM client
class LLMError(Exception):
    pass


def _request(settings, path, body=None, timeout=10):
    url = settings["base_url"].rstrip("/") + path
    headers = {"Content-Type": "application/json", "User-Agent": f"{APP_NAME}/{VERSION}"}
    if settings.api_key:
        headers["Authorization"] = "Bearer " + settings.api_key
    data = json.dumps(body).encode() if body is not None else None
    try:
        return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise LLMError(f"{url} answered HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError(f"Can't reach the AI server at {settings['base_url']} ({getattr(e, 'reason', e)}).") from e


def list_models(settings):
    with _request(settings, "/models", timeout=4) as r:
        return [m["id"] for m in json.load(r).get("data", [])]


def _ollama_exe():
    for c in (shutil.which("ollama"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")):
        if c and os.path.isfile(c):
            return c
    return ""


def ensure_server(settings):
    """Model list from the server; starts a hidden `ollama serve` first if Ollama is installed but not running."""
    try:
        return list_models(settings)
    except LLMError:
        exe = _ollama_exe()
        if not exe or ":11434" not in settings["base_url"]:
            raise
    env = {**os.environ, "OLLAMA_CONTEXT_LENGTH": str(settings["context_tokens"])}
    subprocess.Popen([exe, "serve"], env=env, creationflags=NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(0.5)
        try:
            return list_models(settings)
        except LLMError:
            pass
    raise LLMError("Started Ollama but it did not answer within 15 s.")


THINK = re.compile(r"<think>.*?(</think>|$)", re.S)


def chat(settings, messages, on_delta=None, stop=None, max_tokens=None, temperature=0.2):
    """Streams an OpenAI-compatible chat completion; returns the full text (think blocks removed)."""
    model = settings.get("model") or (list_models(settings) or [""])[0]
    body = {"model": model, "messages": messages, "stream": True, "temperature": temperature,
            "max_tokens": max_tokens or min(4096, settings["context_tokens"] // 3)}
    out = []
    with _request(settings, "/chat/completions", body, timeout=900) as r:
        for raw in r:
            if stop is not None and stop.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                piece = json.loads(payload)["choices"][0].get("delta", {}).get("content") or ""
            except (ValueError, KeyError, IndexError):
                continue
            if piece:
                out.append(piece)
                if on_delta:
                    on_delta(piece)
    return THINK.sub("", "".join(out)).strip()


# ----------------------------------------------------------------------------- print photos
# Phones upload to Google Drive / OneDrive / Dropbox, which sync to a local folder. PartForge watches that
# folder, so every cloud works with no sign-in code of its own.
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VISION_HINTS = ("vl", "vision", "llava", "gemma3", "minicpm-v", "moondream", "pixtral")


def photo_folders():
    """Cloud-synced folders that exist on this PC: [(label, path)]. A Google Drive pick gets its own subfolder."""
    home, out = Path.home(), []
    od = Path(os.environ.get("OneDrive") or home / "OneDrive")
    drives = [Path(f"{c}:/My Drive") for c in "DEFGHIJKLMNOPQRSTUVWXYZ"] + [home / "Google Drive" / "My Drive", home / "My Drive"]
    out += [("Google Drive", d / "PartForge Photos") for d in drives if d.is_dir()]
    out += [(label, p) for label, p in (("OneDrive camera uploads", od / "Pictures" / "Camera Roll"),
                                        ("Dropbox camera uploads", home / "Dropbox" / "Camera Uploads")) if p.is_dir()]
    out.append(("PartForge photo folder (copy photos in yourself)", HOME / "Photo inbox"))
    return out


def new_photos(folder, since, depth=2):
    """Images under folder modified after `since`, newest first. Shallow walk: camera folders nest by year/month."""
    found = []

    def walk(d, level):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if e.is_dir() and level < depth:
                walk(e.path, level + 1)
            elif e.is_file() and os.path.splitext(e.name)[1].lower() in IMAGE_EXT:
                mtime = e.stat().st_mtime
                if mtime > since:
                    found.append((mtime, e.path))
    if folder and os.path.isdir(folder):
        walk(folder, 0)
    return [p for _, p in sorted(found, reverse=True)]


def image_data_url(path, max_px=1280):
    """Phone photos are huge; local vision models want ~1k px. Pillow (optional) shrinks them, else send as-is."""
    try:
        from io import BytesIO
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        img.thumbnail((max_px, max_px))
        buf = BytesIO()
        img.save(buf, "JPEG", quality=85)
        data, mime = buf.getvalue(), "image/jpeg"
    except (ImportError, OSError, ValueError):  # no Pillow, or a format it can't read: send the file as-is
        # ponytail: full-size upload; fine for local servers, slow for big photos
        data = Path(path).read_bytes()
        mime = {".png": "image/png", ".webp": "image/webp"}.get(Path(path).suffix.lower(), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def pick_vision_model(settings, models):
    if settings.get("vision_model") in models:
        return settings["vision_model"]
    return next((m for m in models if any(h in m.lower() for h in VISION_HINTS)), "")


def describe_photos(settings, model, photos, project, notes):
    """A vision model reports what the print photos show; its text feeds the design model (which may be text-only)."""
    params = "; ".join(f"{k}={fmt(p['value'])}{p['unit']}" for k, p in project.params.items())
    text = (f"These are photos of a 3D print of '{project.name}'.\nBrief: {brief_text(project.data['brief'])}\n"
            f"Design parameters: {params}\n"
            f"The user says: {notes or 'nothing'}\n\nDescribe only what you can actually see that matters for a "
            "redesign: fit problems, warping, layer splits, weak or broken features, stringing, sagging overhangs, "
            "sizes (only if a ruler or caliper is visible). Terse bullet points. Say when you can't tell.")
    content = [{"type": "text", "text": text}] + [{"type": "image_url", "image_url": {"url": image_data_url(p)}} for p in photos]
    s = Settings(settings)
    s["model"] = model
    return chat(s, [{"role": "user", "content": content}], max_tokens=800)


def feedback_message(record):
    return (f"Print feedback #{record['id']}: I printed build {record['build']} and took {len(record['photos'])} photo(s).\n"
            f"My notes: {record['notes'] or '(none)'}\n"
            f"What the photos show: {record['observations'] or '(no vision model, so go by my notes)'}\n"
            "Redesign the part to fix these problems and keep what works. Say briefly what you changed.")


def asker(settings):
    return lambda prompt, max_tokens=2000: chat(settings, [{"role": "user", "content": prompt}], max_tokens=max_tokens)


# ----------------------------------------------------------------------------- web research
def _get(url, data=None, timeout=10):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                 headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout)


def parse_ddg(body):
    hits = []
    links = re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', body, re.S)
    if not links:  # lite layout
        links = re.findall(r"href=\"([^\"]+)\" class='result-link'>(.*?)</a>", body, re.S)
        snips = re.findall(r"class='result-snippet'>(.*?)</td>", body, re.S)
    for i, (href, title) in enumerate(links):
        href = html.unescape(href)
        url = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if "duckduckgo.com/y.js" in url or not url.startswith("http"):
            continue  # ads
        clean = lambda s: html.unescape(re.sub(r"<.*?>", "", s)).strip()
        hits.append({"title": clean(title), "url": url, "snippet": clean(snips[i]) if i < len(snips) else ""})
    return hits


def web_search(query, n=5):
    for url in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            with _get(url, {"q": query}) as r:
                hits = parse_ddg(r.read().decode("utf-8", "replace"))
        except OSError as e:
            log.warning("search %s failed: %s", url, e)
            continue
        if hits:
            return hits[:n]
    return []


class _TextOnly(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "svg", "form", "aside"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in self.SKIP

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip and d.strip():
            self.parts.append(d.strip())


def fetch_text(url, limit=2500):
    try:
        with _get(url, timeout=8) as r:
            if "html" not in r.headers.get("Content-Type", ""):
                return ""  # ponytail: PDFs keep only their search snippet; add a PDF text extractor if datasheets matter
            raw = r.read(800_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except (OSError, ValueError) as e:
        log.info("fetch %s failed: %s", url, e)
        return ""
    p = _TextOnly()
    p.feed(raw)
    return re.sub(r"\s+", " ", " ".join(p.parts))[:limit]


BRIEF_FIELDS = [  # key, label, lines, default
    ("name", "Part name", 1, ""),
    ("purpose", "What is it and what does it do?", 3, ""),
    ("mates", "What does it hold or attach to? (parts, model numbers, known dimensions)", 3, ""),
    ("envelope", "Size limits or key dimensions you already know", 2, ""),
    ("material", "Material and process", 1, "PLA, FDM 3D printing"),
    ("machine", "Printer / machine and build volume", 1, ""),
    ("constraints", "Must-haves (snap-fit, screws, disassembly, waterproof, tolerances...)", 3, ""),
    ("avoid", "Things to avoid", 2, ""),
    ("refs", "Links, datasheets or part numbers to look up", 2, ""),
    ("search", "Extra search keywords", 1, ""),
]


def brief_text(brief):
    return "\n".join(f"{label}: {brief[k].strip()}" for k, label, _, _ in BRIEF_FIELDS if brief.get(k, "").strip())


def heuristic_queries(brief):
    first = lambda k: (brief.get(k) or "").strip().splitlines()[0][:80] if (brief.get(k) or "").strip() else ""
    qs = [f"{first('mates')} dimensions mechanical drawing", f"{first('name')} {first('material')} design guidelines",
          f"{first('refs')} datasheet dimensions", f"{first('search')} {first('name')}"]
    return [q.strip() for q in qs if len(q.strip()) > 12][:4] or [brief.get("name", "part") + " design dimensions"]


def make_queries(brief, ask=None):
    if ask:
        try:
            txt = ask("Write 4 short web search queries that would find exact dimensions, datasheets, mechanical "
                      "drawings, standards and proven design practice needed to design this part. Reply with only "
                      "a JSON array of strings.\n\n" + brief_text(brief), 300)
            m = re.search(r"\[.*\]", txt, re.S)
            qs = [q.strip() for q in json.loads(m[0]) if isinstance(q, str) and q.strip()] if m else []
            if qs:
                return qs[:5]
        except (LLMError, ValueError) as e:
            log.info("query generation fell back: %s", e)
    return heuristic_queries(brief)


def gather(queries, per_query=3, max_pages=8):
    seen, sources = set(), []
    for q in queries:
        for h in web_search(q, per_query):
            if h["url"] not in seen:
                seen.add(h["url"])
                sources.append({**h, "query": q})
    sources = sources[:max_pages]
    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        for s, text in zip(sources, ex.map(fetch_text, [s["url"] for s in sources])):
            s["text"] = text
    return sources


def digest(brief, sources, ask=None, start=1, budget_tokens=5000):
    """Research notes with [n] citations plus a ```memory block of facts/questions (when an LLM is available)."""
    if not ask:
        return "\n".join(f"[{start + i}] {s['title']}: {s['snippet']}" for i, s in enumerate(sources))
    per = max(300, budget_tokens * CHARS_PER_TOKEN // max(1, len(sources)))
    body = "\n\n".join(f"[{start + i}] {s['title']} ({s['url']})\n{s['snippet']}\n{s.get('text', '')[:per]}"
                       for i, s in enumerate(sources))
    return ask("Research digest. From the sources below, extract only facts useful for designing this part: exact "
               "dimensions with units, hole positions, clearances, tolerances, standards, material limits, proven "
               "design rules. Terse bullet points, cite sources as [n]. List key dimensions you could not find under "
               "'Measure:'.\nThen output a ```memory block with JSON {\"facts\": {\"snake_case_name\": [value, \"unit\", "
               "\"[n]\"]}, \"questions\": [\"what the user still has to measure or decide\"]}.\n\n# Part\n"
               + brief_text(brief) + "\n\n# Sources\n" + body, 1500)


# ----------------------------------------------------------------------------- parameters
_FUNCS = {"min": min, "max": max, "abs": abs, "round": round, "sqrt": math.sqrt, "sin": math.sin,
          "cos": math.cos, "tan": math.tan, "radians": math.radians, "degrees": math.degrees, "pi": math.pi}
_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call, ast.Add, ast.Sub,
          ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv, ast.USub, ast.UAdd)
_UNITS = {"": 1, "mm": 1, "cm": 10, "m": 1000, "in": 25.4, '"': 25.4, "deg": 1, "°": 1}


def eval_expr(expr, env):
    """Safe arithmetic over parameter names (the Fusion/SolidWorks 'equations' idea)."""
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise ValueError(f"'{expr}': {type(node).__name__} is not allowed in an expression")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            raise ValueError(f"'{expr}': only {', '.join(sorted(_FUNCS))} can be called")
    return float(eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, {**_FUNCS, **env}))


def parse_entry(text):
    """User input -> (value, expr). '60', '2.5 in', '6cm' are values; '=width*2' or 'width*2' is an expression."""
    t = text.strip().lstrip("⌀Ø").strip()
    if t.startswith("="):
        return None, t[1:].strip()
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\s*(mm|cm|m|in|\"|deg|°)?", t, re.I)
    if m:
        return float(m[1]) * _UNITS[(m[2] or "").lower()], ""
    if re.search(r"[A-Za-z_]", t):
        return None, t
    raise ValueError(f"Can't read '{text}'. Type a number (60, 2.5 in) or an expression (=width*2).")


def resolve_params(memory):
    """Evaluate expression parameters in dependency order. Raises ValueError on cycles/unknown names."""
    params = memory["params"]
    env = {k: f["value"] for k, f in memory.get("facts", {}).items() if isinstance(f.get("value"), (int, float))}
    env.update({k: p["value"] for k, p in params.items() if not p.get("expr")})
    pending = {k: p["expr"] for k, p in params.items() if p.get("expr")}
    while pending:
        progress = False
        for k, e in list(pending.items()):
            try:
                env[k] = eval_expr(e, env)
            except NameError:
                continue
            del pending[k]
            progress = True
        if not progress:
            raise ValueError("Circular or unknown reference in: " + ", ".join(f"{k} = {e}" for k, e in pending.items()))
    for k, p in params.items():
        p["value"] = env[k]


def code_params(code):
    """PARAMS literal from model.py without executing it."""
    try:
        for node in ast.parse(code).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PARAMS" for t in node.targets):
                return ast.literal_eval(node.value)
    except (SyntaxError, ValueError):
        pass
    return {}


def _spec(v):
    v = list(v) if isinstance(v, (list, tuple)) else [v]
    default, unit, note, lo, hi = (v + ["mm", "", None, None])[:5]
    return float(default), unit, note, lo, hi


def sync_params(memory, old_code, new_code):
    """New model code redefines the parameter set. Live values win, unless the code changed that default."""
    old_defs, new_defs, params = code_params(old_code), code_params(new_code), memory["params"]
    out = {}
    for name, spec in new_defs.items():
        default, unit, note, lo, hi = _spec(spec)
        cur = params.get(name)
        changed_default = name in old_defs and _spec(old_defs[name])[0] != default
        keep = cur is not None and not changed_default
        out[name] = {"value": cur["value"] if keep else default, "expr": cur.get("expr", "") if keep else "",
                     "unit": unit, "note": note, "min": lo, "max": hi}
    memory["params"] = out
    return sorted(set(out) - set(params)), sorted(set(params) - set(out))


# ----------------------------------------------------------------------------- AI reply protocol
BLOCK = re.compile(r"```([\w-]*)[ \t]*\r?\n(.*?)```", re.S)


def _loads_loose(text):
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", text))


def parse_reply(text):
    """(model code or None, memory dict or None, [search queries])."""
    code, mem, searches = None, None, []
    for lang, body in BLOCK.findall(THINK.sub("", text)):
        lang = lang.lower()
        if "def build(" in body:
            code = body.strip() + "\n"
        elif lang == "memory":
            try:
                mem = _loads_loose(body)
            except ValueError:
                log.info("unparseable memory block")
        elif lang == "search":
            searches += [l.strip() for l in body.splitlines() if l.strip()]
    return code, mem, searches


def condense(text):
    """What history keeps of a reply: code is already in context as model.py, so don't pay for old copies."""
    def rep(m):
        lang, body = m[1].lower(), m[2]
        if "def build(" in body:
            return f"[model.py updated, {body.count(chr(10))} lines]"
        if lang == "memory":
            return "[part notes updated]"
        if lang == "search":
            return "[web search: " + "; ".join(l.strip() for l in body.splitlines() if l.strip()) + "]"
        return m[0]
    return BLOCK.sub(rep, THINK.sub("", text)).strip()


def check_code(code, strict=True):
    """strict = AI-written code: no filesystem/network/process access. Code the user edits is theirs to run."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"model.py line {e.lineno}: {e.msg}"
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "build" not in names:
        return "model.py must define build(P)"
    if not strict:
        return ""
    bad = {"os", "subprocess", "socket", "urllib", "requests", "shutil", "ctypes", "sys"}
    for n in ast.walk(tree):
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
        hit = [m for m in mods if m.split(".")[0] in bad]
        if hit or (isinstance(n, ast.Name) and n.id in ("open", "exec", "eval", "__import__")):
            return f"model.py uses {hit[0] if hit else n.id}, which isn't allowed in part models"
    return ""


def merge_memory(memory, upd):
    """Apply a ```memory block. Returns human-readable parameter changes."""
    changes = []
    for k, v in (upd.get("params") or {}).items():
        if k not in memory["params"]:
            continue
        try:
            val = float(v.get("value") if isinstance(v, dict) else v)
        except (TypeError, ValueError):
            continue
        p = memory["params"][k]
        if abs(p["value"] - val) > 1e-9:
            changes.append(f"{k}: {fmt(p['value'])} → {fmt(val)} {p['unit']}")
        p.update(value=val, expr="")
    for k, v in (upd.get("facts") or {}).items():
        v = list(v) if isinstance(v, (list, tuple)) else [v]
        memory["facts"][k] = {"value": v[0], "unit": v[1] if len(v) > 1 else "", "source": str(v[2]) if len(v) > 2 else ""}
    if isinstance(upd.get("features"), list):
        memory["features"] = [f for f in upd["features"] if isinstance(f, dict) and f.get("name")]
    memory["decisions"] = (memory["decisions"] + [str(d) for d in upd.get("decisions") or []])[-40:]
    if isinstance(upd.get("questions"), list):
        memory["questions"] = [str(q) for q in upd["questions"]][:12]
    return changes


# ----------------------------------------------------------------------------- project
STARTER = '''import Part
from FreeCAD import Vector

# name: (default, unit, note, min, max)
PARAMS = {
    "length": (60, "mm", "overall length (X)", 10, 250),
    "width": (40, "mm", "overall width (Y)", 10, 250),
    "thickness": (4, "mm", "plate thickness (Z)", 1, 50),
    "hole_d": (3.4, "mm", "M3 clearance hole", 1, 20),
    "hole_spacing": (45, "mm", "hole centre distance (X)", 5, 240),
}


def build(P):
    plate = Part.makeBox(P["length"], P["width"], P["thickness"])
    cx, cy = P["length"] / 2, P["width"] / 2
    for x in (cx - P["hole_spacing"] / 2, cx + P["hole_spacing"] / 2):
        plate = plate.cut(Part.makeCylinder(P["hole_d"] / 2, P["thickness"] + 2, Vector(x, cy, -1)))
    return {"plate": plate}


def dimensions(P):
    L, W, T = P["length"], P["width"], P["thickness"]
    x0 = L / 2 - P["hole_spacing"] / 2
    return [("length", (0, 0, T), (L, 0, T), "top"),
            ("width", (L, 0, T), (L, W, T), "top"),
            ("hole_spacing", (x0, W / 2, T), (x0 + P["hole_spacing"], W / 2, T), "top"),
            ("hole_d", (x0 - P["hole_d"] / 2, W / 2, T), (x0 + P["hole_d"] / 2, W / 2, T), "top", "dia"),
            ("thickness", (0, 0, 0), (0, 0, T), "front")]
'''


def list_templates():
    """[(name, description, code)] from templates/*.py; first line is '# Template: name | description'."""
    out = []
    for f in sorted((HERE / "templates").glob("*.py")):
        head, _, body = f.read_text("utf-8").partition("\n")
        name, _, desc = head.removeprefix("# Template:").partition("|")
        out.append((name.strip() or f.stem, desc.strip(), body.lstrip()))
    return out


def new_data(brief):
    return {"version": 1, "app": VERSION, "brief": brief, "created": time.time(),
            "research": {"queries": [], "sources": [], "notes": ""},
            "memory": {"params": {}, "features": [], "facts": {}, "decisions": [], "questions": [], "changes": []},
            "chat": [], "summary": "", "markups": [], "builds": 0, "revisions": [],
            "prints": [], "photo_since": time.time()}


class Project:
    def __init__(self, folder, data, code):
        self.folder, self.data, self.code = Path(folder), data, code

    json_path = property(lambda self: self.folder / "project.json")
    model_path = property(lambda self: self.folder / "model.py")
    out = property(lambda self: self.folder / "out")
    memory = property(lambda self: self.data["memory"])
    params = property(lambda self: self.data["memory"]["params"])
    name = property(lambda self: self.data["brief"].get("name") or self.folder.name)

    @classmethod
    def create(cls, parent, brief, code=STARTER):
        parent = Path(parent)
        parent.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", brief.get("name", "")).strip("-")[:40] or "part"
        folder, n = parent / slug, 2
        while folder.exists():
            folder, n = parent / f"{slug}-{n}", n + 1
        folder.mkdir()
        p = cls(folder, new_data(brief), code)
        sync_params(p.memory, "", code)
        p.save()
        return p

    @classmethod
    def open(cls, path):
        path = Path(path)
        folder = path.parent if path.is_file() else path
        data = json.loads((folder / "project.json").read_text("utf-8"))
        base = new_data(data.get("brief", {}))
        for k, v in base.items():            # older files gain any new keys
            data.setdefault(k, v)
        for k, v in base["memory"].items():
            data["memory"].setdefault(k, v)
        code = (folder / "model.py").read_text("utf-8") if (folder / "model.py").exists() else STARTER
        return cls(folder, data, code)

    def save(self):
        self.data["saved"] = time.time()
        write_atomic(self.json_path, json.dumps(self.data, indent=1))
        write_atomic(self.model_path, self.code)

    def set_code(self, code):
        added, removed = sync_params(self.memory, self.code, code)
        self.code = code
        return added, removed

    def values(self):
        return {k: p["value"] for k, p in self.params.items()}

    def log_change(self, text):
        self.memory["changes"] = (self.memory["changes"] + [time.strftime("%H:%M ") + text])[-30:]

    def chat_add(self, role, content, **extra):
        self.data["chat"].append({"role": role, "content": content, "t": time.time(), **extra})

    def last_report(self):
        try:
            return json.loads((self.out / "report.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None


# ----------------------------------------------------------------------------- FreeCAD bridge
def run_build(freecad_cmd, project_folder, code, values, timeout=240):
    """Build in a hidden freecadcmd process. Returns the report dict (ok False + error on failure)."""
    out = Path(project_folder) / "out"
    out.mkdir(exist_ok=True)
    HOME.mkdir(parents=True, exist_ok=True)
    worker = HOME / "fc_worker.py"          # short, space-free path: freecadcmd chokes on long ones
    src = (HERE / "fc_worker.py").read_text("utf-8")
    if not worker.exists() or worker.read_text("utf-8") != src:
        write_atomic(worker, src)
    write_atomic(out / "build_model.py", code)
    write_atomic(out / "params.json", json.dumps(values))
    (out / "report.json").unlink(missing_ok=True)
    env = {**os.environ, "PF_MODEL": str(out / "build_model.py"), "PF_PARAMS": str(out / "params.json"), "PF_OUT": str(out),
           "PF_IMPORTS": str(Path(project_folder) / "imports")}
    try:
        p = subprocess.run([freecad_cmd, str(worker)], env=env, cwd=str(out), capture_output=True,
                           timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"FreeCAD took longer than {timeout} s and was stopped."}
    except OSError as e:
        return {"ok": False, "error": f"Couldn't start FreeCAD ({freecad_cmd}): {e}"}
    try:
        return json.loads((out / "report.json").read_text("utf-8"))
    except (OSError, ValueError):
        tail = (p.stdout + p.stderr).decode("utf-8", "replace")[-1500:]
        return {"ok": False, "error": "FreeCAD exited without a report:\n" + tail}


def report_problems(rep, params, bed=None):
    """Warnings worth the AI's attention on an otherwise successful build."""
    probs = []
    for name, p in (rep or {}).get("parts", {}).items():
        if bed and any(a > b + 1e-6 for a, b in zip(sorted(p["bbox"]), sorted(bed))):
            probs.append(f"{name}: {' x '.join(fmt(v) for v in p['bbox'])} mm doesn't fit the "
                         f"{' x '.join(fmt(v) for v in bed)} mm printer in any orientation")
        if not p["valid"]:
            probs.append(f"{name}: invalid solid")
        if p["solids"] != 1:
            probs.append(f"{name}: {p['solids']} separate solids (expected 1)")
        if not p["watertight"]:
            probs.append(f"{name}: mesh not watertight (bodies touching only along an edge?)")
        if p["min"][2] < -0.01:
            probs.append(f"{name}: extends below Z=0")
    for d in (rep or {}).get("dims", []):
        want = params.get(d["param"], {}).get("value")
        if want is not None and abs(d["measured"] - want) > 0.05:
            probs.append(f"dimension {d['param']} is drawn {fmt(d['measured'])} but the parameter is {fmt(want)}")
    if (rep or {}).get("dim_error"):
        probs.append("dimensions() failed: " + rep["dim_error"].strip().splitlines()[-1])
    return probs


def report_text(rep, params, bed=None):
    if not rep:
        return "Not built yet."
    if not rep.get("ok"):
        return "BUILD FAILED:\n" + rep.get("error", "")[-1500:]
    lines = [f"{n}: {' x '.join(fmt(v) for v in p['bbox'])} mm, {fmt(p['volume'] / 1000)} cm3"
             for n, p in rep["parts"].items()]
    probs = report_problems(rep, params, bed)
    return "\n".join(lines + (["PROBLEMS: " + "; ".join(probs)] if probs else ["All checks passed."]))


# ----------------------------------------------------------------------------- context assembly
SYSTEM = """You are PartForge, a mechanical design engineer who designs one part by writing FreeCAD Python.

Change the part ONLY by replying with the complete model.py in a ```python block:

```python
import Part
from FreeCAD import Vector
import math

# name: (default, unit, note, min, max). Every dimension a user might tweak is a parameter.
PARAMS = {"length": (60, "mm", "overall length", 10, 250)}

def build(P):
    # P = {name: value}. Return a Part.Shape, or {"part_name": Shape} for several printed parts.
    return Part.makeBox(P["length"], 20, 5)

def dimensions(P):
    # Driving dimensions drawn on the engineering drawing. The user edits them there.
    # (param, point A, point B, view[, "dia"]). The distance A-B in that view must equal P[param].
    return [("length", (0, 0, 5), (P["length"], 0, 5), "top")]
```

Rules: millimetres. Z up, part resting on Z=0, front face at minimum Y. Views: "top" looks down Z (X,Y),
"front" looks from -Y (X,Z), "right" looks from +X (Y,Z). Only Part, FreeCAD, math. Never read files or the network;
bodies the user modelled by hand are available as load("name.step") (listed under IMPORTED BODIES).
Derive dependent sizes from P inside build() so the part stays valid when the user changes a value.
Put every parameter in dimensions() on the view where it reads best. Printed parts must be one watertight solid.
The LIVE parameter values below are what the user set on the drawing; they win over your defaults.
To change a value without rewriting the code, send only a memory block with "params".

Keep notes about the part in a ```memory block (JSON, any subset):
{"params": {"name": value}, "facts": {"name": [value, "unit", "source"]},
 "features": [{"name": "mounting boss", "params": ["boss_d", "boss_h"], "note": "why it exists"}],
 "decisions": ["short decision + reason"], "questions": ["what you need the user to decide or measure"]}
Keep "features" in sync with the model: they are drawn as the Part Map the user reads.
To look something up online, add a ```search block with one query per line.
Redlines are the user's markups on the drawing: fix exactly what they point at.
Reply with short prose first, then the blocks."""

FIRST_TURN_TEMPLATE = ("Adapt the starting model (a verified template) to the brief and research: rename, add and "
                       "remove parameters and features as needed, keep what already works. Include features, decisions "
                       "and open questions in a memory block.")
FIRST_TURN = ("Create the first version of this part from the brief and research. Replace the starter plate. "
              "Include features, decisions and open questions in a memory block.")


def memory_text(project, report=None):
    m = project.memory
    ps = [f"{k} = {fmt(p['value'])} {p['unit']}" + (f" (= {p['expr']})" if p.get("expr") else "")
          + (f" [{p['note']}]" if p.get("note") else "") for k, p in m["params"].items()]
    parts = ["LIVE PARAMETERS: " + ("; ".join(ps) or "none")]
    if m["features"]:
        parts.append("FEATURES: " + "; ".join(f"{f['name']} ({', '.join(f.get('params', []))})" for f in m["features"]))
    if m["facts"]:
        parts.append("FACTS: " + "; ".join(f"{k} = {v['value']} {v['unit']} {v['source']}".strip() for k, v in m["facts"].items()))
    if m["decisions"]:
        parts.append("DECISIONS: " + "; ".join(m["decisions"][-10:]))
    if m["questions"]:
        parts.append("OPEN QUESTIONS: " + "; ".join(m["questions"]))
    if m["changes"]:
        parts.append("RECENT CHANGES: " + "; ".join(m["changes"][-10:]))
    reds = [r for r in project.data["markups"] if r["status"] != "resolved"]
    if reds:
        parts.append("REDLINES: " + "; ".join(f"#{r['id']} {r['view'].upper()} view near {r['where']}: {r['text']}" for r in reds))
    for p in project.data.get("prints", [])[-2:]:
        parts.append(f"PRINT FEEDBACK #{p['id']} (printed build {p['build']}): notes: {p['notes'] or '-'}; "
                     f"photos show: {(p.get('observations') or '-')[:600]}")
    imports = (report or {}).get("imports") or {}
    if imports:
        parts.append("IMPORTED BODIES (load(name)); bbox min xyz, max xyz: " + "; ".join(
            f"{k}: {v if isinstance(v, str) else ' '.join(fmt(x) for x in v)}" for k, v in imports.items()))
    return "\n".join(parts)


def build_messages(project, settings, report):
    ctx = settings["context_tokens"]
    reply_reserve = min(4096, ctx // 3)
    notes = project.data["research"]["notes"]
    notes_budget = int(ctx * 0.15) * CHARS_PER_TOKEN
    if len(notes) > notes_budget:
        notes = notes[:notes_budget] + "\n[...notes trimmed]"
    system = "\n\n".join(s for s in [
        SYSTEM, load_modules(settings),
        "# BRIEF\n" + brief_text(project.data["brief"]),
        "# PART MEMORY\n" + memory_text(project, report),
        "# RESEARCH NOTES\n" + notes if notes.strip() else "",
        "# LAST BUILD\n" + report_text(report, project.params, settings.get("bed")),
        "# CURRENT model.py\n```python\n" + project.code + "```",
        "# EARLIER CONVERSATION (summary)\n" + project.data["summary"] if project.data["summary"] else "",
    ] if s)
    budget = max(500, ctx - reply_reserve - est_tokens(system))
    live = [{"role": m["role"], "content": m["content"]} for m in project.data["chat"]
            if not m.get("archived") and m["role"] in ("user", "assistant")]
    return [{"role": "system", "content": system}] + fit_history(live, budget)


def summarize_old(project, settings, ask):
    """Token Thrift rolling compression: once live history passes half the budget, fold the oldest into a summary.
    Returns (summary, archived message indexes) or None; the caller applies it on the UI thread."""
    budget = settings["context_tokens"] // 4
    idx = [i for i, m in enumerate(project.data["chat"]) if not m.get("archived") and m["role"] in ("user", "assistant")]
    msgs = [project.data["chat"][i] for i in idx]
    if sum(est_tokens(m["content"]) for m in msgs) < budget * 2:
        return None
    old, _ = split_for_summary(msgs, budget)
    if not old:
        return None
    text = "\n\n".join(f"{m['role']}: {m['content']}" for m in old)
    summary = ask("Summarize this design conversation. Keep every decision, requirement, number and open issue "
                  "a continuing engineer needs; drop chit-chat.\n\nPrevious summary:\n" + (project.data["summary"] or "none")
                  + "\n\nConversation:\n" + text, 800)
    return (summary.strip(), idx[:len(old)]) if summary.strip() else None


# ----------------------------------------------------------------------------- self-check
if __name__ == "__main__":
    assert parse_entry("60") == (60.0, "") and parse_entry("2.5 in") == (63.5, "") and parse_entry("6cm") == (60.0, "")
    assert parse_entry("=width*2") == (None, "width*2") and parse_entry("width + 3") == (None, "width + 3")
    assert parse_entry("⌀3.4") == (3.4, "")
    try:
        parse_entry("12..5")
        raise AssertionError("bad number accepted")
    except ValueError:
        pass
    mem = {"params": {"a": {"value": 10, "expr": ""}, "b": {"value": 0, "expr": "a*2"}, "c": {"value": 0, "expr": "b+hole"}},
           "facts": {"hole": {"value": 3, "unit": "mm", "source": ""}}}
    resolve_params(mem)
    assert mem["params"]["b"]["value"] == 20 and mem["params"]["c"]["value"] == 23
    mem["params"]["a"]["expr"] = "c"
    try:
        resolve_params(mem)
        raise AssertionError("cycle accepted")
    except ValueError:
        pass
    try:
        eval_expr("__import__('os')", {})
        raise AssertionError("call accepted")
    except ValueError:
        pass

    msgs = [{"role": "user", "content": "x" * 100} for _ in range(3)]
    assert len(fit_history(msgs, 60)) == 2 and len(fit_history(msgs, 5)) == 1 and fit_history([], 9) == []
    assert [len(p) for p in split_for_summary(msgs, 60)] == [1, 2]

    m = {"params": {}}
    sync_params(m, "", STARTER)
    m["params"]["length"]["value"] = 99
    newer = STARTER.replace('"width": (40', '"width": (50').replace('    "hole_d"', '    "rib": (2, "mm", "rib"),\n    "hole_d"')
    added, removed = sync_params(m, STARTER, newer)
    assert m["params"]["length"]["value"] == 99, "live value must survive a code update"
    assert m["params"]["width"]["value"] == 50, "a changed default is the AI's deliberate change"
    assert added == ["rib"] and removed == []

    reply = "Done.\n```python\n" + STARTER + "```\n```memory\n{\"params\": {\"length\": 70,}, \"questions\": [\"q\"]}\n```\n```search\nm3 insert\n```"
    code, mem_upd, searches = parse_reply(reply)
    assert code and mem_upd["params"]["length"] == 70 and searches == ["m3 insert"]
    assert condense(reply).startswith("Done.\n[model.py updated")
    assert check_code(STARTER) == "" and "os" in check_code("import os\ndef build(P): pass")
    assert "open" in check_code("def build(P): return open('x')")

    body = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=1">A <b>t</b></a>'
            '<a class="result__snippet" href="x">snip &amp; more</a>')
    ts = list_templates()
    assert len(ts) >= 4 and all("def build(" in c and check_code(c) == "" for _, _, c in ts), "templates"
    part = {"min": [0, 0, 0], "valid": True, "solids": 1, "watertight": True}
    probs = report_problems({"parts": {"p": {**part, "bbox": [260, 240, 10]}}}, {}, [220, 220, 250])
    assert probs and "fit" in probs[0], probs
    assert not report_problems({"parts": {"p": {**part, "bbox": [240, 100, 10]}}}, {}, [220, 220, 250])  # fits upright
    assert parse_ddg(body) == [{"title": "A t", "url": "https://example.com/a", "snippet": "snip & more"}]
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        old, new, deep = Path(d, "old.jpg"), Path(d, "new.PNG"), Path(d, "2026", "10", "deep.jpg")
        deep.parent.mkdir(parents=True)
        for f in (old, new, deep, Path(d, "notes.txt")):
            f.write_bytes(b"\x89PNG\r\n\x1a\n")
        os.utime(old, (1000, 1000))
        assert set(new_photos(d, 5000)) == {str(new), str(deep)}, "new images only, two folders deep, any case"
        assert image_data_url(new).startswith("data:image/")
    assert pick_vision_model({"vision_model": ""}, ["qwen2.5-coder:7b", "qwen2.5vl:7b"]) == "qwen2.5vl:7b"
    assert pick_vision_model({"vision_model": ""}, ["qwen2.5-coder:7b"]) == ""
    rec = {"id": 2, "build": 7, "photos": ["a", "b"], "notes": "lid too tight", "observations": ""}
    assert "lid too tight" in feedback_message(rec) and "go by my notes" in feedback_message(rec)
    print("core self-check passed")
