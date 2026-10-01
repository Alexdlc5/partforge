"""End-to-end test of the real window against the fake AI server and real FreeCAD (and real web search).

    python tests/test_app.py [screenshot_dir]
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PARTFORGE_HOME"] = tempfile.mkdtemp(prefix="pf_home_")
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import fake_llm  # noqa: E402
import app as appmod  # noqa: E402
import core  # noqa: E402

SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="pf_shots_"))
SHOTS.mkdir(parents=True, exist_ok=True)
warnings = []
appmod.messagebox.showwarning = lambda title, msg, **kw: warnings.append(msg)

srv, url = fake_llm.start()
settings = core.Settings.load()
settings.update(base_url=url, projects_dir=tempfile.mkdtemp(prefix="pf_projects_"))
a = appmod.App(settings)
a.attributes("-topmost", True)
a.geometry("1440x880+20+20")


def wait(cond, what, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        a.update()
        if cond():
            return
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for: {what}")


def idle():
    wait(lambda: not a.building and not a.busy_llm and not a.build_timer and a.q.empty(), "app idle", 180)
    for _ in range(10):
        a.update()
        time.sleep(0.02)


def shot(name):
    from PIL import ImageGrab
    a.update()
    time.sleep(0.3)
    a.update()
    x, y = a.winfo_rootx(), a.winfo_rooty()
    ImageGrab.grab((x, y, x + a.winfo_width(), y + a.winfo_height())).save(SHOTS / f"{name}.png")


wait(lambda: a.llm_ok, "fake AI detected")
shot("0_welcome")

# 1. New part: brief -> web research -> first AI design -> FreeCAD build
proj = core.Project.create(settings["projects_dir"], {"name": "Monitor bracket", "purpose": "L bracket holding a 5 inch panel",
                                                      "material": "PLA, FDM 3D printing", "mates": "M4 screws into a desk"})
a.load_project(proj)
a.run_research(then_design=True)
wait(lambda: "wall_h" in a.project.params and a.report and "bracket" in a.report["parts"], "first design built", 180)
idle()
if a.project.data["research"]["sources"]:   # live web search; DuckDuckGo sometimes asks for a human check
    assert a.project.memory["facts"]["m4_clearance"]["value"] == 4.5
else:
    notes = " ".join(m["content"] for m in a.project.data["chat"])
    assert "temporarily blocking" in notes, "research must explain why it found nothing"
    print("note: web search was blocked by DuckDuckGo this run; checked the user-facing message instead")
assert len(a.project.memory["features"]) == 3
assert {d["param"] for d in a.report["dims"]} == set(a.project.params)
assert core.report_problems(a.report, a.project.params) == []
shot("1_first_design")

# 2. Edit a driving dimension on the drawing: the Modify box, Enter -> rebuild -> table/map/memory updated
tid = next(t for t, d in a.drawing.dimtexts.items() if d["param"] == "base_len")
a.drawing.edit_param("base_len", a.drawing.coords(tid))
box = a.nametowidget(a.drawing.itemcget(a.drawing.editing, "window"))
ent = next(w for w in box.winfo_children() if w.winfo_class() == "Entry")
ent.delete(0, "end")
ent.insert(0, "70")
shot("2_modify_box")
ent.focus_force()
ent.event_generate("<Return>")
if a.drawing.editing:   # Windows withholds keyboard focus while another app is in front: commit like Enter
    a.drawing.commit_edit()
idle()
assert a.project.params["base_len"]["value"] == 70
assert a.report["parts"]["bracket"]["bbox"][0] == 70, a.report["parts"]["bracket"]["bbox"]
assert a.ptable.tree.set("base_len", "value") == "70"
assert any("base_len: 50 → 70" in c for c in a.project.memory["changes"])
assert "base_len = 70 mm" in core.build_messages(a.project, settings, a.report)[0]["content"]

# 3. Equation, units, range guard, undo
assert a.set_param("wall_h", "=base_len/2", "test")
idle()
assert a.project.params["wall_h"]["value"] == 35 and a.report["parts"]["bracket"]["bbox"][2] == 35
a.set_param("base_len", "3.5 in", "test")
idle()
assert abs(a.project.params["wall_h"]["value"] - 44.45) < 1e-9, "equation follows its driver"
assert not a.set_param("thickness", "40", "test") and "range" in warnings[-1]
a.undo()
idle()
assert a.project.params["base_len"]["value"] == 70 and a.project.params["wall_h"]["value"] == 35
shot("3_equation")

# 4. AI breaks the model -> build fails -> automatic repair -> builds again
a.send("BREAKME")
wait(lambda: "failed to build" in " ".join(fake_llm.calls), "auto-repair request", 120)
idle()
assert a.last_report["ok"] and a.report["parts"], "repair did not rebuild"

# 5. Redline on the drawing -> AI changes a parameter -> rebuild; redline marked sent
a.add_redline("front", 2, 20, "wall too thin, make it stiffer")
idle()
assert a.project.params["thickness"]["value"] == 6
assert a.project.data["markups"][0]["status"] == "addressed"
a.nb.select(0)
shot("4_redline")

# 6. Part Map and research tabs render
a.nb.select(1)
shot("5_part_map")
a.nb.select(2)
shot("6_research")
a.nb.select(0)

# 7. model.py edited in an external editor -> watcher rebuilds
a.save()
src = a.project.model_path.read_text("utf-8").replace('"hole_d": (4.5', '"hole_d": (5.5')
time.sleep(0.05)
a.project.model_path.write_text(src, encoding="utf-8")
wait(lambda: a.project.params["hole_d"]["value"] == 5.5, "external edit picked up", 20)
idle()
assert any(abs(d["measured"] - 5.5) < 1e-6 for d in a.report["dims"] if d["param"] == "hole_d")

# 8. Send-to pipeline with a user-added app (pythonw, harmless) and the quick-send switch
pyw = str(Path(sys.executable).with_name("pythonw.exe"))
settings["apps_custom"].append({"id": "custom-test", "name": "Test Slicer", "kind": "slicer", "sends": "stl",
                                "exe": pyw, "args": '-c "pass" {files}'})
a.set_quick(appmod.apps.get(settings, "custom-test"))
assert a.send_btn.cget("text") == "▶ Send to Test Slicer"
a.quick_send()
assert "Sent 1 file to Test Slicer" in a.st_build.cget("text"), a.st_build.cget("text")
a.set_quick(appmod.apps.get(settings, "creality-print"))

# 9. Revisions: save A, change, restore A
a.save_revision("first release")
assert a.project.data["revisions"][0]["rev"] == "A" and appmod.App._rev_letter(26) == "AA"
a.set_param("width", "44", "test")
idle()
a.restore_revision(a.project.data["revisions"][0])
idle()
assert a.project.params["width"]["value"] == 30 and a.report["parts"]["bracket"]["bbox"][1] == 30

# 10. Drawing export (SVG) and printer bed check
svg = SHOTS / "drawing.svg"
a.export_svg(str(svg))
text = svg.read_text("utf-8")
assert text.startswith("<svg") and "<polyline" in text and ">70<" in text and "REV A" in text
settings["bed"] = [40, 40, 40]
assert any("doesn't fit" in p for p in appmod.core.report_problems(a.report, a.project.params, settings["bed"]))
a.drawing.redraw()
shot("7_bed_warning")
settings["bed"] = [220, 220, 250]

# 10b. Print feedback loop: a phone photo syncs into the folder -> button lights up -> feedback -> vision model
#      looks at it -> design AI redesigns -> rebuild -> "send it again"
inbox = Path(tempfile.mkdtemp(prefix="pf_inbox_"))
settings["photo_inbox"] = str(inbox)
from PIL import Image  # noqa: E402
Image.new("RGB", (900, 600), (205, 120, 40)).save(inbox / "IMG_0001.jpg")
wait(lambda: a.inbox_count == 1, "new photo noticed", 15)
assert "1 new" in a.photo_btn.cget("text")
shot("7b_photo_waiting")
dlg = appmod.FeedbackDialog(a, a.inbox_photos(), str(inbox))
dlg.issues["Layer split / weak"].set(True)
dlg.notes.insert("1.0", "cracked at the bend after one day")
dlg.finish(True)
assert dlg.result[0] == [str(inbox / "IMG_0001.jpg")] and dlg.result[1].startswith("Layer split / weak; cracked")
a.submit_feedback(*dlg.result)
rec = a.project.data["prints"][-1]
wait(lambda: rec["status"].startswith("redesigned"), "redesign built from print feedback", 120)
idle()
assert (a.project.folder / rec["photos"][0]).exists(), "photo copied into the project"
assert "layer split" in rec["observations"].lower(), rec["observations"]
assert "vision with 1 image(s)" in fake_llm.calls
assert a.project.params["thickness"]["value"] == 8 and a.report["parts"]["bracket"]["bbox"][2] == 35
assert "PRINT FEEDBACK #1" in core.build_messages(a.project, settings, a.report)[0]["content"]
wait(lambda: a.inbox_count == 0, "inbox cleared after import", 15)
assert a.photo_btn.cget("text") == "📷 Print feedback", "button resets right after sending"
a.nb.select(a.prints_tab)
shot("7c_prints_tab")
a.nb.select(0)

# 11. New-part dialog with a template -> the enclosure builds as two printable parts
dlg = appmod.IntakeDialog(a)
dlg.widgets["name"].insert(0, "Sensor box")
combo = next(v for v in dlg.body.children.values() if v.winfo_class() == "TCombobox")
dlg.template.set(combo.cget("values")[1])
dlg.finish(False)
brief, research, template = dlg.result
assert brief["name"] == "Sensor box" and not research and "def build(" in template
enclosure = next(c for n, _, c in appmod.core.list_templates() if n.startswith("Enclosure"))
a.load_project(appmod.core.Project.create(settings["projects_dir"], {"name": "Sensor box"}, enclosure))
a.request_build("user", 0)
idle()
assert set(a.report["parts"]) == {"box", "lid"} and not appmod.core.report_problems(a.report, a.project.params)
shot("8_enclosure_template")
a.set_param("inner_l", "100", "test")
idle()
assert a.report["parts"]["box"]["bbox"][0] == 104

# 11b. "I asked for a screw and got a plate": typing "screw" suggests the screw template, which builds a real
#      threaded screw; an unmatched part with no AI gets an honest message, not a placeholder plate
dlg = appmod.IntakeDialog(a)
dlg.widgets["name"].insert(0, "screw")
dlg.suggest()
assert dlg.template.get().startswith("Screw / bolt"), dlg.template.get()
dlg.finish(False)
_, _, screw_code = dlg.result
a.load_project(appmod.core.Project.create(settings["projects_dir"], {"name": "screw"}, screw_code))
a.request_build("user", 0)
idle()
screw = a.report["parts"]["screw"]
x, y, z = sorted(screw["bbox"])
assert screw["valid"] and screw["watertight"] and z >= 1.8 * y, screw
shot("8b_screw_template")
blank = appmod.core.Project.create(settings["projects_dir"], {"name": "phone stand"})
blank.data["designed"], blank.memory["params"] = False, {}
a.llm_ok = False
a.load_project(blank)
a._first_design()
idle()
assert a.report is None and not a.build_timer, "no placeholder part without an AI"
assert "none matches" in a.project.data["chat"][-1]["content"]
assert "Nothing designed yet" in appmod.core.build_messages(a.project, settings, None)[0]["content"]
a.llm_ok = True

# 12. Close saves everything; reopening restores it
a.show_welcome()
shot("9_welcome_setup")
assert "FreeCAD" in a.setup_lbl.cget("text") and "Local AI" in a.setup_lbl.cget("text")
a.load_project(appmod.core.Project.open(a.project.folder))
a.load_project(appmod.core.Project.open(sorted(Path(settings["projects_dir"]).glob("Monitor*"))[0]))
idle()
folder = a.project.folder
a.dirty = True
a.on_close()
data = json.loads((folder / "project.json").read_text("utf-8"))
assert data["memory"]["params"]["thickness"]["value"] == 6 and data["builds"] >= 6
reopened = core.Project.open(folder)
assert reopened.code == src.rstrip() + "\n" or reopened.code == src
srv.shutdown()
print("GUI end-to-end test passed. Screenshots:", SHOTS)
print("AI calls:", len(fake_llm.calls), "| builds:", data["builds"], "| warnings:", warnings)
