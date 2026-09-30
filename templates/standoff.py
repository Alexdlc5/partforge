# Template: Spacer / standoff | Round spacer with a through hole (PCB standoffs, shims)
import Part
from FreeCAD import Vector

# name: (default, unit, note, min, max)
PARAMS = {
    "outer_d": (7, "mm", "outside diameter", 3, 80),
    "hole_d": (3.4, "mm", "through hole (M3 clearance)", 1, 70),
    "height": (10, "mm", "spacer height", 0.6, 150),
}


def build(P):
    body = Part.makeCylinder(P["outer_d"] / 2, P["height"])
    return {"spacer": body.cut(Part.makeCylinder(P["hole_d"] / 2, P["height"] + 2, Vector(0, 0, -1)))}


def dimensions(P):
    r, h = P["outer_d"] / 2, P["hole_d"] / 2
    return [("outer_d", (-r, 0, P["height"]), (r, 0, P["height"]), "top", "dia"),
            ("hole_d", (-h, 0, P["height"]), (h, 0, P["height"]), "top", "dia"),
            ("height", (-r, 0, 0), (-r, 0, P["height"]), "front")]
