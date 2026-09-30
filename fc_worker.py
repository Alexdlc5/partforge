"""Runs inside FreeCAD's console (freecadcmd), never in the app's own Python.

Env: PF_MODEL (model.py), PF_PARAMS (params.json), PF_OUT (output folder).
Builds the part, checks it, projects hidden-line views, maps driving dimensions onto
those views and exports STEP / STL / FCStd. Everything the app needs lands in report.json.
"""
import json
import math
import os
import time
import traceback

T0 = time.time()
OUT = os.environ["PF_OUT"]
rep = {"ok": False, "parts": {}, "views": {}, "dims": [], "files": {}}


def save():
    rep["seconds"] = round(time.time() - T0, 2)
    tmp = os.path.join(OUT, "report.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rep, f, separators=(",", ":"))
    os.replace(tmp, os.path.join(OUT, "report.json"))


try:
    import FreeCAD as App
    import MeshPart
    import Part
    import TechDraw

    def mat(rows):
        m = App.Matrix()
        (m.A11, m.A12, m.A13), (m.A21, m.A22, m.A23), (m.A31, m.A32, m.A33) = rows
        return m

    # Rotate so the viewer looks down -Z; projectEx then keeps (x, y) as sheet coords.
    VIEWS = {"top": mat([(1, 0, 0), (0, 1, 0), (0, 0, 1)]),        # X right, Y up
             "front": mat([(1, 0, 0), (0, 0, 1), (0, -1, 0)]),     # X right, Z up
             "right": mat([(0, 1, 0), (0, 0, 1), (1, 0, 0)]),      # Y right, Z up
             "iso": (App.Rotation(App.Vector(1, 0, 0), -55) * App.Rotation(App.Vector(0, 0, 1), -35)).toMatrix()}

    def project(shape, view):
        s = shape.copy()
        s.transformShape(VIEWS[view], True)
        r = TechDraw.projectEx(s, App.Vector(0, 0, 1))
        out = {}
        for key, idx in (("vis", (0, 1, 3)), ("hid", (5, 8))):
            lines = []
            for i in idx:
                sh = r[i]
                if sh is None or sh.isNull():
                    continue
                for e in sh.Edges:
                    lines.append([[round(p.x, 2), round(p.y, 2)] for p in e.discretize(Deflection=0.05)])
            out[key] = lines
        pts = [p for line in out["vis"] + out["hid"] for p in line]
        out["bbox"] = ([min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
                       if pts else [0, 0, 0, 0])
        return out

    with open(os.environ["PF_MODEL"], encoding="utf-8") as f:
        code = f.read()
    with open(os.environ["PF_PARAMS"], encoding="utf-8") as f:
        live = json.load(f)
    imports = os.environ.get("PF_IMPORTS", "")

    def load(name):
        """Bodies modelled by hand elsewhere (File > Import body). Only files in the project's imports folder."""
        path = os.path.join(imports, os.path.basename(name))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"No imported body '{name}'. Import it with File > Import body.")
        return Part.read(path)

    rep["imports"] = {}
    for fname in sorted(os.listdir(imports)) if os.path.isdir(imports) else []:
        try:
            bb = load(fname).BoundBox
            rep["imports"][fname] = [round(v, 2) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]
        except Exception as e:
            rep["imports"][fname] = str(e)

    ns = {"__name__": "partforge_model", "load": load}
    exec(compile(code, "model.py", "exec"), ns)
    defaults = {k: (v[0] if isinstance(v, (list, tuple)) else v) for k, v in ns.get("PARAMS", {}).items()}
    P = {**defaults, **{k: v for k, v in live.items() if k in defaults}}

    result = ns["build"](P)
    shapes = result if isinstance(result, dict) else {"part": result}
    if not shapes:
        raise ValueError("build(P) returned nothing")

    doc = App.newDocument("model")
    for name, s in shapes.items():
        if not isinstance(s, Part.Shape) or s.isNull():
            raise TypeError(f"build(P) returned {type(s).__name__} for '{name}', expected a non-empty Part.Shape")
        bb = s.BoundBox
        mesh = MeshPart.meshFromShape(Shape=s, LinearDeflection=0.05, AngularDeflection=0.3)
        stl = os.path.join(OUT, f"{name}.stl")
        mesh.write(stl)
        rep["files"][name + ".stl"] = stl
        rep["parts"][name] = {"valid": s.isValid(), "solids": len(s.Solids),
                              "bbox": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)],
                              "min": [round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3)],
                              "volume": round(s.Volume, 1),
                              "watertight": bool(mesh.isSolid() and not mesh.hasNonManifolds())}
        doc.addObject("Part::Feature", name).Shape = s

    whole = Part.makeCompound(list(shapes.values()))
    for v in VIEWS:
        rep["views"][v] = project(whole, v)

    try:
        for d in ns.get("dimensions", lambda P: [])(P):
            param, a, b, view = d[:4]
            M = VIEWS[view]
            pa, pb = M.multVec(App.Vector(*a)), M.multVec(App.Vector(*b))
            rep["dims"].append({"param": param, "view": view, "kind": d[4] if len(d) > 4 else "linear",
                                "a": [round(pa.x, 3), round(pa.y, 3)], "b": [round(pb.x, 3), round(pb.y, 3)],
                                "measured": round(math.hypot(pb.x - pa.x, pb.y - pa.y), 3)})
    except Exception:
        rep["dim_error"] = traceback.format_exc(limit=3)

    step = os.path.join(OUT, "model.step")
    whole.exportStep(step)
    rep["files"]["model.step"] = step
    fcstd = os.path.join(OUT, "model.FCStd")
    doc.saveAs(fcstd)
    rep["files"]["model.FCStd"] = fcstd
    rep["ok"] = True
except Exception as exc:
    # Only the model's own frames: that's what the AI (and the user) can fix.
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "model.py"]
    rep["error"] = "".join(traceback.format_list(frames) + traceback.format_exception_only(type(exc), exc))
save()
