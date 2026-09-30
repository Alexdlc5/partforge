"""Send-to connectors: slicers, CAD programs, code editors.

Adding an app to PartForge = one dict in BUILTIN:
    id     unique key
    name   what the user sees
    kind   slicer | cad | editor | other
    sends  stl | step | fcstd | code | folder     (what gets handed over, see SENDS)
    find   glob patterns for the .exe ({PF} Program Files, {PF86} Program Files (x86), {LP} %LOCALAPPDATA%\\Programs)
    exe    fixed path instead of find, or SYSTEM for "whatever Windows opens this file type with"
    args   argument template, default "{files}". Placeholders: {files} {file} {folder}
Users add their own in Manage Apps (stored in settings["apps_custom"]) and can repoint any exe (settings["app_paths"]).
"""
import glob
import os
import shlex
import shutil
import subprocess
from pathlib import Path

SYSTEM = "(Windows default app)"
SENDS = {"stl": "Printable parts (.stl)", "step": "STEP model (.step)", "fcstd": "FreeCAD document (.FCStd)",
         "code": "Parametric source (model.py)", "folder": "Output folder"}
KINDS = {"slicer": "Slice & print", "cad": "Model by hand", "editor": "Edit source", "other": "Other"}

BUILTIN = [
    {"id": "creality-print", "name": "Creality Print", "kind": "slicer", "sends": "stl",
     "find": [r"{PF}\Creality\Creality Print*\CrealityPrint.exe"]},
    {"id": "orca", "name": "OrcaSlicer", "kind": "slicer", "sends": "stl", "find": [r"{PF}\OrcaSlicer\orca-slicer.exe"]},
    {"id": "bambu", "name": "Bambu Studio", "kind": "slicer", "sends": "stl", "find": [r"{PF}\Bambu Studio\bambu-studio.exe"]},
    {"id": "prusa", "name": "PrusaSlicer", "kind": "slicer", "sends": "stl", "find": [r"{PF}\Prusa3D\PrusaSlicer\prusa-slicer.exe"]},
    {"id": "cura", "name": "UltiMaker Cura", "kind": "slicer", "sends": "stl",
     "find": [r"{PF}\UltiMaker Cura*\UltiMaker-Cura.exe", r"{PF}\Ultimaker Cura*\Cura.exe"]},
    {"id": "freecad", "name": "FreeCAD", "kind": "cad", "sends": "fcstd",
     "find": [r"{PF}\FreeCAD*\bin\freecad.exe", r"{LP}\FreeCAD*\bin\freecad.exe"]},
    {"id": "solidworks", "name": "SOLIDWORKS", "kind": "cad", "sends": "step",
     "find": [r"{PF}\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"]},
    {"id": "step-default", "name": "Default STEP app (Fusion, Onshape…)", "kind": "cad", "sends": "step", "exe": SYSTEM},
    {"id": "vscode", "name": "VS Code", "kind": "editor", "sends": "code",
     "find": [r"{LP}\Microsoft VS Code\Code.exe", r"{PF}\Microsoft VS Code\Code.exe"]},
    {"id": "notepad", "name": "Notepad", "kind": "editor", "sends": "code", "find": [r"C:\Windows\notepad.exe"]},
    {"id": "explorer", "name": "File Explorer", "kind": "other", "sends": "folder", "exe": SYSTEM},
]


class SendError(Exception):
    pass


def _expand(pattern):
    env = os.environ
    return (pattern.replace("{PF}", env.get("ProgramFiles", r"C:\Program Files"))
            .replace("{PF86}", env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            .replace("{LP}", os.path.join(env.get("LOCALAPPDATA", ""), "Programs")))


def locate(app):
    exe = app.get("exe", "")
    if exe == SYSTEM or (exe and os.path.isfile(exe)):
        return exe
    for pat in app.get("find", []):
        hits = sorted(glob.glob(_expand(pat)), reverse=True)   # newest version folder first
        if hits:
            return hits[0]
    return ""


def all_apps(settings):
    """Built-ins (with any user-chosen exe) + user-added apps, each with 'path' ('' = not installed)."""
    out = []
    for a in BUILTIN:
        a = {**a, "builtin": True}
        if settings.get("app_paths", {}).get(a["id"]):
            a["exe"] = settings["app_paths"][a["id"]]
        out.append(a)
    out += [{**a, "builtin": False} for a in settings.get("apps_custom", [])]
    for a in out:
        a["path"] = locate(a)
    return out


def get(settings, app_id):
    return next((a for a in all_apps(settings) if a["id"] == app_id), None)


def quick_app(settings):
    """The quick-send app, falling back to the first installed slicer."""
    apps = all_apps(settings)
    chosen = next((a for a in apps if a["id"] == settings.get("quick_send") and a["path"]), None)
    return chosen or next((a for a in apps if a["kind"] == "slicer" and a["path"]), None)


def files_for(app, project_folder, report):
    folder = Path(project_folder)
    files = (report or {}).get("files", {})
    kind = app["sends"]
    if kind == "stl":
        paths = [p for n, p in files.items() if n.endswith(".stl")]
    elif kind == "step":
        paths = [files["model.step"]] if "model.step" in files else []
    elif kind == "fcstd":
        paths = [files["model.FCStd"]] if "model.FCStd" in files else []
    elif kind == "code":
        paths = [str(folder / "model.py")]
    else:
        paths = [str(folder / "out")]
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        raise SendError("Nothing to send yet: build the part first.")
    return paths


def command(app, paths):
    args = shlex.split(app.get("args") or "{files}", posix=False)
    cmd = [app["path"]]
    for a in args:
        a = a.strip('"')
        if a == "{files}":
            cmd += paths
        else:
            cmd.append(a.replace("{file}", paths[0]).replace("{folder}", str(Path(paths[0]).parent)))
    return cmd


def send(app, project_folder, report):
    """Open the part in the app. Returns a status line."""
    if not app.get("path"):
        raise SendError(f"{app['name']} isn't installed here. Point PartForge at it in Send ▸ Manage apps.")
    paths = files_for(app, project_folder, report)
    if app["path"] == SYSTEM:
        for p in paths:
            os.startfile(p)  # noqa: whatever Windows associates with the file type
    else:
        subprocess.Popen(command(app, paths), close_fds=True)
    return f"Sent {len(paths)} file{'s' * (len(paths) > 1)} to {app['name']}."


def editor_path():
    """A code editor for 'edit source' (VS Code if present, else Notepad)."""
    for a in BUILTIN:
        if a["kind"] == "editor" and locate(a):
            return locate(a)
    return shutil.which("notepad") or ""


if __name__ == "__main__":
    fake = {"id": "x", "name": "X", "kind": "slicer", "sends": "stl", "path": r"C:\x.exe", "args": '--load {file} "{files}"'}
    assert command(fake, ["a.stl", "b.stl"]) == [r"C:\x.exe", "--load", "a.stl", "a.stl", "b.stl"]
    assert command({**fake, "args": ""}, ["a.stl"]) == [r"C:\x.exe", "a.stl"]
    s = {"apps_custom": [{"id": "c1", "name": "Mine", "kind": "cad", "sends": "step", "exe": r"C:\nope.exe"}], "quick_send": "c1"}
    apps = all_apps(s)
    assert apps[-1]["name"] == "Mine" and apps[-1]["path"] == ""
    assert quick_app(s) is None or quick_app(s)["kind"] == "slicer"   # uninstalled pick falls back to a slicer
    for a in apps:
        print(f"{a['name']:<40} {a['path'] or '-'}")
    print("apps self-check passed")
