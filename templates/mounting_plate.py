# Template: Mounting plate | Flat plate with two screw holes
# Keywords: plate, mounting plate, base plate, adapter plate, mount, flat
import Part
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
