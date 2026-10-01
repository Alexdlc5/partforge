# Template: Washer | Flat washer with ISO 7089 proportions
# Keywords: washer, flat washer, ring, shim ring, spacer ring
import Part
from FreeCAD import Vector

# name: (default, unit, note, min, max)
PARAMS = {
    "inner_d": (8.4, "mm", "hole (M8 clearance)", 1, 100),
    "outer_d": (16, "mm", "outside diameter", 3, 150),
    "thickness": (1.6, "mm", "thickness", 0.4, 10),
}


def build(P):
    ring = Part.makeCylinder(P["outer_d"] / 2, P["thickness"])
    return {"washer": ring.cut(Part.makeCylinder(P["inner_d"] / 2, P["thickness"] + 2, Vector(0, 0, -1)))}


def dimensions(P):
    t, r, h = P["thickness"], P["outer_d"] / 2, P["inner_d"] / 2
    return [("outer_d", (-r, 0, t), (r, 0, t), "top", "dia"),
            ("inner_d", (-h, 0, t), (h, 0, t), "top", "dia"),
            ("thickness", (-r, 0, 0), (-r, 0, t), "front")]
