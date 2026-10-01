# Template: Enclosure with lid | Open box plus a push-fit lid with a locating lip (prints as two parts)
# Keywords: box, enclosure, case, housing, container, lid, project box
import Part
from FreeCAD import Vector

# name: (default, unit, note, min, max)
PARAMS = {
    "inner_l": (80, "mm", "inside length (X)", 10, 240),
    "inner_w": (50, "mm", "inside width (Y)", 10, 240),
    "inner_h": (30, "mm", "inside height (Z)", 5, 200),
    "wall": (2, "mm", "wall thickness", 1.2, 6),
    "floor": (2, "mm", "floor thickness", 1, 6),
    "lid_t": (2, "mm", "lid plate thickness", 1, 6),
    "lip_h": (4, "mm", "lid lip depth into the box", 2, 15),
    "fit": (0.2, "mm", "clearance per side between lip and wall (PLA push fit)", 0, 1),
}


def build(P):
    w = P["wall"]
    L, W = P["inner_l"] + 2 * w, P["inner_w"] + 2 * w
    box = Part.makeBox(L, W, P["floor"] + P["inner_h"])
    box = box.cut(Part.makeBox(P["inner_l"], P["inner_w"], P["inner_h"] + 1, Vector(w, w, P["floor"])))
    # Lid printed plate-down beside the box, lip pointing up.
    ox = L + 10
    lid = Part.makeBox(L, W, P["lid_t"], Vector(ox, 0, 0))
    ll, lw = P["inner_l"] - 2 * P["fit"], P["inner_w"] - 2 * P["fit"]
    lip = Part.makeBox(ll, lw, P["lip_h"], Vector(ox + w + P["fit"], w + P["fit"], P["lid_t"]))
    lip = lip.cut(Part.makeBox(ll - 2 * w, lw - 2 * w, P["lip_h"] + 1, Vector(ox + 2 * w + P["fit"], 2 * w + P["fit"], P["lid_t"])))
    return {"box": box, "lid": lid.fuse(lip).removeSplitter()}


def dimensions(P):
    w, f = P["wall"], P["floor"]
    H = f + P["inner_h"]
    return [("inner_l", (w, w, H), (w + P["inner_l"], w, H), "top"),
            ("inner_w", (w, w, H), (w, w + P["inner_w"], H), "top"),
            ("wall", (0, 0, H), (w, 0, H), "top"),
            ("inner_h", (w, 0, f), (w, 0, H), "front"),
            ("floor", (0, 0, 0), (0, 0, f), "front"),
            ("lid_t", (P["inner_l"] + 2 * w + 10, 0, 0), (P["inner_l"] + 2 * w + 10, 0, P["lid_t"]), "front"),
            ("lip_h", (P["inner_l"] + 3 * w + 10 + P["fit"], 0, P["lid_t"]),
             (P["inner_l"] + 3 * w + 10 + P["fit"], 0, P["lid_t"] + P["lip_h"]), "front")]
