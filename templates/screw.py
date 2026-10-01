# Template: Screw / bolt | Hex-head machine screw with a real, printable ISO thread
# Keywords: screw, bolt, threaded, thread, fastener, machine screw, hex bolt, hex screw
import Part
from FreeCAD import Vector

# name: (default, unit, note, min, max)
PARAMS = {
    "d": (8, "mm", "thread diameter, M8 (FDM prints M6 and larger reliably)", 3, 30),
    "pitch": (1.25, "mm", "thread pitch (ISO coarse: M6 1.0, M8 1.25, M10 1.5)", 0.4, 3.5),
    "length": (25, "mm", "threaded length under the head", 4, 200),
    "head_af": (13, "mm", "hex head across flats", 4, 50),
    "head_h": (5.3, "mm", "head height", 1.5, 20),
}


def build(P):
    # Printed head-down: the head sits on the bed and the thread grows straight up, no supports needed.
    head = hex_prism(P["head_af"], P["head_h"])
    thread = threaded_rod(P["d"], P["pitch"], P["length"], z0=P["head_h"])
    return {"screw": head.fuse(thread).removeSplitter()}


def dimensions(P):
    h, L, d, af = P["head_h"], P["length"], P["d"], P["head_af"]
    corner = af / 3 ** 0.5
    return [("length", (d / 2, 0, h), (d / 2, 0, h + L), "front"),
            ("head_h", (-corner, 0, 0), (-corner, 0, h), "front"),
            ("d", (-d / 2, 0, h + L), (d / 2, 0, h + L), "front"),
            ("head_af", (0, -af / 2, 0), (0, af / 2, 0), "right")]
