"""A tiny OpenAI-compatible streaming server with canned engineering answers, for testing without a model."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BRACKET = '''import Part
from FreeCAD import Vector

# name: (default, unit, note, min, max)
PARAMS = {
    "base_len": (50, "mm", "base length (X)", 20, 200),
    "width": (30, "mm", "bracket width (Y)", 10, 120),
    "thickness": (4, "mm", "wall thickness", 2, 12),
    "wall_h": (35, "mm", "upright height (Z)", 10, 150),
    "hole_d": (4.5, "mm", "M4 clearance", 2, 12),
}


def build(P):
    t = P["thickness"]
    base = Part.makeBox(P["base_len"], P["width"], t)
    wall = Part.makeBox(t, P["width"], P["wall_h"])
    body = base.fuse(wall).removeSplitter()
    hole = Part.makeCylinder(P["hole_d"] / 2, t + 2, Vector(P["base_len"] * 0.65, P["width"] / 2, -1))
    return {"bracket": body.cut(hole)}


def dimensions(P):
    L, W, T, H = P["base_len"], P["width"], P["thickness"], P["wall_h"]
    x = L * 0.65
    return [("base_len", (0, 0, 0), (L, 0, 0), "front"),
            ("wall_h", (0, 0, 0), (0, 0, H), "front"),
            ("thickness", (L, 0, 0), (L, 0, T), "front"),
            ("width", (L, 0, T), (L, W, T), "top"),
            ("hole_d", (x - P["hole_d"] / 2, W / 2, T), (x + P["hole_d"] / 2, W / 2, T), "top", "dia")]
'''
BROKEN = BRACKET.replace('return {"bracket": body.cut(hole)}', 'return {"bracket": body.cut(undefined_hole)}')

MEMORY = {"features": [{"name": "base plate", "params": ["base_len", "width", "thickness"], "note": "screws to the desk"},
                       {"name": "upright wall", "params": ["wall_h", "thickness"], "note": "holds the panel"},
                       {"name": "mounting hole", "params": ["hole_d"], "note": "M4 screw"}],
          "decisions": ["L-bracket: simplest printable shape, no supports"],
          "questions": ["What screw will you use to mount it?"]}

calls = []


def answer(messages):
    last = messages[-1]["content"]
    calls.append(last[:80])
    if "web search queries" in last:
        return '["M4 screw clearance hole size", "3D printed L bracket design wall thickness PLA"]'
    if last.startswith("Research digest"):
        return ("- M4 clearance hole: 4.5 mm normal fit [1]\n- PLA brackets: 3–4 walls, 4 mm+ thickness for load [2]\n"
                '```memory\n{"facts": {"m4_clearance": [4.5, "mm", "[1]"]}, "questions": ["How much load?"]}\n```')
    if last.startswith("Summarize this design conversation"):
        return "User wants an M4 L-bracket; thickness raised for stiffness."
    if "BREAKME" in last:
        return "Trying a variant.\n```python\n" + BROKEN + "```"
    if "failed to build" in last or "now fails" in last or "rejected" in last:
        return "Fixed the undefined name.\n```python\n" + BRACKET + "```"
    if last.startswith("Redline"):
        return 'Thickened the wall as marked.\n```memory\n{"params": {"thickness": 6}, "decisions": ["thicker wall per redline"]}\n```'
    if "Create the first version" in last:
        return "Here's an L-bracket to start.\n```python\n" + BRACKET + "```\n```memory\n" + json.dumps(MEMORY) + "\n```"
    return "Noted."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "fake-coder"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = answer(req["messages"])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(text), 40):
            chunk = {"choices": [{"delta": {"content": text[i:i + 40]}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
