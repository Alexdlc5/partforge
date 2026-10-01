"""Proven geometry helpers available to every model.py (runs inside FreeCAD).

Small local models get raw OpenCASCADE sweeps and booleans wrong; these are tested from M3 to M20 and always
return one valid, watertight solid. The worker injects every name in __all__ into the model's namespace.
"""
import math

import Part
from FreeCAD import Vector

__all__ = ["ISO_PITCH", "HEX_AF", "FDM_CLEARANCE", "threaded_rod", "hex_prism", "nut_blank"]

# ISO 261 coarse pitch and ISO 4032 hex across-flats, by nominal diameter (mm)
ISO_PITCH = {2: 0.4, 2.5: 0.45, 3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75, 16: 2.0, 20: 2.5, 24: 3.0}
HEX_AF = {2: 4, 2.5: 5, 3: 5.5, 4: 7, 5: 8, 6: 10, 8: 13, 10: 16, 12: 18, 16: 24, 20: 30, 24: 36}
FDM_CLEARANCE = 0.3   # radial play so a printed nut turns on a printed bolt

# Drawings show threads simplified (ISO 6410), and hidden-line projection of a real helix looked at down its axis
# takes minutes. The worker rebuilds the model with DRAWING on, so every thread becomes a plain cylinder.
DRAWING = False
USED = [False]


def threaded_rod(d, pitch, length, z0=0.0):
    """External 60° thread, major diameter d, from z0 to z0+length.

    Built as a twisted sweep of the thread cross-section (no booleans), so it is always valid.
    For an internal thread (nut, tapped hole) cut threaded_rod(d + 2 * FDM_CLEARANCE, ...) from the body.
    """
    USED[0] = True
    if DRAWING:
        return Part.makeCylinder(d / 2, length, Vector(0, 0, z0))
    r_maj, r_min = d / 2, d / 2 - 0.6134 * pitch
    pts = []
    for i in range(48):
        f = i / 48
        tri = 1 - abs(2 * f - 1)                                   # crest at f = 0.5, root at 0 / 1
        r = r_min + (r_maj - r_min) * min(1.0, max(0.0, (tri - 0.08) / 0.84))
        pts.append(Vector(r * math.cos(2 * math.pi * f), r * math.sin(2 * math.pi * f), 0))
    section = Part.BSplineCurve()
    section.interpolate(pts, PeriodicFlag=True)
    sweep = Part.BRepOffsetAPI.MakePipeShell(Part.Wire(Part.makeLine(Vector(0, 0, 0), Vector(0, 0, length))))
    sweep.setAuxiliarySpine(Part.Wire(Part.makeHelix(pitch, length, r_maj * 2)), True, 0)
    sweep.add(Part.Wire(section.toShape()))
    sweep.build()
    sweep.makeSolid()
    rod = sweep.shape()
    rod = Part.Solid(rod) if rod.ShapeType == "Shell" else rod
    rod.translate(Vector(0, 0, z0))
    return rod


def hex_prism(across_flats, height, z0=0.0):
    """Hexagonal prism (bolt head, nut body) centred on the Z axis."""
    r = across_flats / math.sqrt(3)
    pts = [Vector(r * math.cos(math.radians(60 * i)), r * math.sin(math.radians(60 * i)), z0) for i in range(7)]
    return Part.Face(Part.makePolygon(pts)).extrude(Vector(0, 0, height))


def nut_blank(d, height, across_flats=None):
    """Hex nut with an internal thread that fits threaded_rod(d, ...) printed on an FDM printer."""
    pitch = ISO_PITCH.get(d, max(0.5, round(d * 0.15, 2)))
    af = across_flats or HEX_AF.get(d, d * 1.6)
    return hex_prism(af, height).cut(threaded_rod(d + 2 * FDM_CLEARANCE, pitch, height + 2 * pitch, -pitch))
