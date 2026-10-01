"""Score how well a local AI designs real parts through PartForge's own pipeline.

    python tests/eval_models.py                         # every case, current Settings (Ollama by default)
    python tests/eval_models.py --model qwen2.5-coder:14b --cases screw,gear
    python tests/eval_models.py --compare               # with vs without PartForge's template matching
    python tests/eval_models.py --selftest              # check the shape tests on the verified templates (no AI)

Each case: brief -> AI first design (same prompts as the app) -> FreeCAD build -> up to 2 automatic repairs.
Score out of 100: builds 40, checks clean 20, looks like the requested part 30, driving dimensions present and true 10.
Results are written to tests/eval_results/<model>-<date>.md.
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PARTFORGE_HOME", tempfile.mkdtemp(prefix="pf_eval_home_"))
import core  # noqa: E402


# --- shape tests: cheap geometry facts from the build report -------------------------------------------
def _dims(p):
    return sorted(p["bbox"])


def rod(p):          # screw / bolt / pin: long, round, curved
    a, b, c = _dims(p)
    return c >= 1.8 * b and b <= 1.2 * a and p["curved_faces"] >= 1


def nut(p):          # stubby, near-square (hex) footprint, hollow: curved inside, well under its box volume
    x, y, z = p["bbox"]
    foot = max(x, y)
    return (min(x, y) >= 0.8 * foot and 0.3 * foot <= z <= 1.2 * foot and p["curved_faces"] >= 1
            and p["volume"] < 0.85 * x * y * z)


def ring(p):         # washer: thin, round, holed
    a, b, c = _dims(p)
    return a <= 0.35 * c and abs(b - c) <= 0.1 * c and p["curved_faces"] >= 2 and p["volume"] < 0.75 * a * b * c


def round_part(p):   # knob, wheel: round footprint
    x, y, _ = p["bbox"]
    return abs(x - y) <= 0.1 * max(x, y) and p["curved_faces"] >= 1


def gear(p):         # teeth: many faces around a round footprint
    return round_part(p) and p["faces"] >= 40


def anything(p):
    return True


CASES = [  # name, brief purpose, shape test
    ("screw", "an M8 screw about 30 mm long", rod),
    ("hex nut", "an M8 nut to go on a printed M8 screw", nut),
    ("washer", "a flat washer for an M6 bolt", ring),
    ("knob", "a round grip knob for a 6 mm D-shaft potentiometer", round_part),
    ("spur gear", "a 20 tooth spur gear, module 1.5, 6 mm bore", gear),
    ("phone stand", "a desk stand that holds a phone at about 60 degrees", anything),
    ("cable clip", "a clip that holds a 6 mm cable against a desk edge, screw mounted", anything),
    ("wall hook", "a hook for hanging a backpack, with two screw holes", anything),
]


def score(rep, params, shape_test):
    if not rep or not rep.get("ok"):
        return 0, "did not build"
    parts = list(rep["parts"].values())
    probs = core.report_problems(rep, params)
    shape_ok = any(shape_test(p) for p in parts)
    dims = {d["param"] for d in rep.get("dims", [])}
    dims_ok = bool(dims & set(params)) and not any("drawn" in p for p in probs)   # some params (fit clearance) aren't distances
    pts = 40 + (20 if not probs else 0) + (30 if shape_ok else 0) + (10 if dims_ok else 0)
    why = [] if shape_ok else ["doesn't look like the part"]
    return pts, "; ".join(why + probs[:2]) or "ok"


def design(settings, name, purpose, use_templates, fc):
    """One case through the app's pipeline. Returns (report, params, tries, notes)."""
    brief = {"name": name, "purpose": purpose, "material": "PLA, FDM 3D printing"}
    t = core.match_template(brief) if use_templates else None
    proj = core.Project.create(tempfile.mkdtemp(prefix="pf_eval_"), brief, t[2] if t else core.STARTER)
    if not t:
        proj.data["designed"] = False
        proj.memory["params"] = {}
    ask = core.FIRST_TURN_TEMPLATE if t else core.FIRST_TURN
    rep, notes = None, [f"template: {t[0]}" if t else "from scratch"]
    for attempt in range(3):
        proj.chat_add("user", ask)
        reply = core.chat(settings, core.build_messages(proj, settings, rep))
        proj.chat_add("assistant", core.condense(reply))
        code, mem, _ = core.parse_reply(reply)
        if mem:
            core.merge_memory(proj.memory, mem)
        if code:
            err = core.check_code(code)
            if err:
                ask = f"Your model.py was rejected: {err}. Send a corrected complete model.py."
                notes.append("rejected: " + err)
                continue
            proj.set_code(code)
            proj.data["designed"] = True
        elif not proj.data["designed"]:
            ask = "You didn't send model.py. Send the complete model.py in a ```python block."
            notes.append("no code")
            continue
        rep = core.run_build(fc, proj.folder, proj.code, proj.values())
        if rep.get("ok"):
            return rep, proj.params, attempt + 1, notes
        err = rep.get("error", "")
        notes.append("build failed: " + (err.strip().splitlines() or ["?"])[-1][:90])
        ask = f"Your model.py failed to build:\n{err[-1500:]}\nFix it."
    return rep, proj.params, 3, notes


def selftest(fc):
    """The shape tests must recognise the verified templates, or the scores mean nothing."""
    expect = {"Screw / bolt": rod, "Nut": nut, "Washer": ring, "Spacer / standoff": round_part}
    for name, _, code in core.list_templates():
        p = core.Project.create(tempfile.mkdtemp(prefix="pf_eval_"), {"name": name}, code)
        rep = core.run_build(fc, p.folder, p.code, p.values())
        assert rep["ok"], (name, rep.get("error"))
        part = next(iter(rep["parts"].values()))
        verdicts = {f.__name__: f(part) for f in (rod, nut, ring, round_part, gear)}
        print(f"{name:<20} {verdicts}")
        if name in expect:
            assert expect[name](part), f"{name} should pass {expect[name].__name__}"
        if name in ("Mounting plate", "L-bracket", "Enclosure with lid"):
            assert not any(f(part) for f in (rod, nut, ring)), f"{name} must not pass as a fastener"
        if name in ("Screw / bolt", "Washer"):
            assert not nut(part), f"{name} must not pass as a nut"
    print("eval selftest passed")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    ap.add_argument("--model")
    ap.add_argument("--cases", help="comma-separated case names")
    ap.add_argument("--compare", action="store_true", help="run with and without template matching")
    ap.add_argument("--no-templates", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    fc = core.find_freecad()
    if not fc:
        sys.exit("FreeCAD not found.")
    if a.selftest:
        return selftest(fc)
    settings = core.Settings.load()
    if a.url:
        settings["base_url"] = a.url
    models = core.ensure_server(settings)
    settings["model"] = a.model or settings["model"] or (models[0] if models else "")
    if settings["model"] not in models:
        sys.exit(f"Model '{settings['model']}' isn't installed. Available: {', '.join(models) or 'none'}")
    cases = [c for c in CASES if not a.cases or c[0] in a.cases.split(",")]
    modes = [True, False] if a.compare else [not a.no_templates]
    rows, totals = [], {}
    for use_t in modes:
        for name, purpose, test in cases:
            t0 = time.time()
            try:
                rep, params, tries, notes = design(settings, name, purpose, use_t, fc)
                pts, why = score(rep, params, test)
            except core.LLMError as e:
                pts, why, tries, notes = 0, f"AI error: {e}", 0, []
            mode = "templates" if use_t else "raw"
            totals.setdefault(mode, []).append(pts)
            rows.append(f"| {mode} | {name} | {pts} | {tries} | {time.time() - t0:.0f}s | {why} | {'; '.join(notes)} |")
            print(rows[-1], flush=True)
    summary = " · ".join(f"{m}: {sum(v) / len(v):.0f}/100" for m, v in totals.items())
    out = ROOT / "tests" / "eval_results" / f"{settings['model'].replace(':', '_').replace('/', '_')}-{time.strftime('%Y%m%d-%H%M')}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(f"# {settings['model']}  ({summary})\n\n| mode | case | score | tries | time | verdict | notes |\n"
                   "|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"\n{summary}\nwritten to {out}")


if __name__ == "__main__":
    main()
