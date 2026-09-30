# Template: L-bracket | Screw-down angle bracket with a mounting hole
import Part
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
