# Template: Nut | Hex nut with a printable internal thread that fits the screw template
# Keywords: nut, hex nut, threaded nut, fastener, captive nut
import Part
from FreeCAD import Vector

# name: (default, unit, note, min, max)
PARAMS = {
    "d": (8, "mm", "thread diameter it fits, M8", 3, 30),
    "pitch": (1.25, "mm", "thread pitch, must match the screw", 0.4, 3.5),
    "height": (6.8, "mm", "nut height (ISO 4032: 0.8 x d)", 2, 40),
    "af": (13, "mm", "across flats (wrench size)", 5, 60),
    "clearance": (0.3, "mm", "radial play so a printed nut turns on a printed screw", 0, 0.8),
}


def build(P):
    body = hex_prism(P["af"], P["height"])
    hole = threaded_rod(P["d"] + 2 * P["clearance"], P["pitch"], P["height"] + 2 * P["pitch"], z0=-P["pitch"])
    return {"nut": body.cut(hole)}


def dimensions(P):
    h, af, d = P["height"], P["af"], P["d"]
    corner = af / 3 ** 0.5
    return [("height", (corner, 0, 0), (corner, 0, h), "front"),
            ("af", (0, -af / 2, h), (0, af / 2, h), "right"),
            ("d", (-d / 2, 0, h), (d / 2, 0, h), "top", "dia")]
