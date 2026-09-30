# PartForge: market and roadmap (September 2026)

## The market today

| Product | Price | Output | Where it runs | The complaint |
|---|---|---|---|---|
| Zoo Text-to-CAD | free 20 credits/mo, Pro up to $99/mo | STEP / STL B-rep, sliders | cloud | complex parts "rough to wrong" |
| AdamCAD | $5.99–17.99/mo | OpenSCAD → STL, sliders | cloud (browser) | shallow parametrics; STEP only on its roadmap |
| CADAgent | Fusion license + API costs | native Fusion history | cloud API | needs Fusion; unreliable on complex parts |
| Fusion / SOLIDWORKS / Onshape assistants | bundled in $500–4000/yr seats | commands, Q&A, early drawing automation | mixed | simple ops only, or documentation help rather than geometry |
| OpenSCAD / FreeCAD + a local LLM (DIY) | free | code | local | no UI, no memory, no checks; FreeCAD scripts from LLMs fail often |

Sources: [texocad overview](https://blog.texocad.ai/posts/ai-cad-software-2026),
[AdamCAD review](https://pasqualepillitteri.it/en/news/3372/adamcad-text-to-cad-ai-review-2026),
[Zoo](https://www.toolmage.com/en/tool/zoo/), [local AI for 3D printing](https://localaimaster.com/blog/local-ai-3d-printing),
[Leo AI on what engineers need](https://www.getleo.ai/blog/best-text-to-cad-ai-tools-2026).

**The gaps PartForge targets:**
1. **Orphaned geometry.** Text-to-CAD output usually arrives as a STEP with no history.
   PartForge keeps a live parametric model with named driving dimensions.
2. **Shallow controls.** Sliders are all you get elsewhere. PartForge has driving dimensions on a
   real drawing, equations, ranges, undo and revisions, the vocabulary engineers already use.
3. **Unreliable first shots.** PartForge rebuilds deterministically, checks the result, repairs
   automatically, and offers templates. Small local models get a working base and a feedback loop
   instead of one blind attempt.
4. **Privacy and cost.** Nothing leaves the machine except web searches. There are no credits and no
   subscription for inference. The competitors all run in the cloud.
5. **Drawing automation beats generation.** This is where the time savings are, and PartForge is
   drawing-first.

## Who pays

- **Makers and 3D-print hobbyists (volume):** want "describe it, print it". They value templates,
  Send-to-slicer and the bed-fit check.
- **Small shops and engineers (willingness to pay):** want privacy for customer IP, STEP output,
  drawings, and revisions. They value drawing export, revisions and a CAD round trip.

## Suggested pricing

- **Free:** everything in this release. Local AI, unlimited parts. Free is what wins against
  credit-metered cloud tools.
- **Pro, about $8/month or $79 one-time:** PDF drawing sheets with sizes and borders, the template
  library, printer profiles with one-click slicing to G-code, revision diff, and commercial use.
- **Shop, about $25 per seat per month:** a shared part library, drawing standards (ISO/ASME title
  blocks, tolerance blocks), a team template pack, and priority support.

## Built in this release

- Brief → research → design pipeline; editable drawing; equations; Part Map; redlines; auto-repair
- Send-to pipeline covering slicers, CAD programs and editors; user-added apps; quick-send switch
- Model by hand: FreeCAD / STEP app / editor round trip with live rebuild; STEP import as a body
- Templates, bed-fit check, revisions, SVG drawing export, setup checklist, installer

## Next, in order of value per effort

1. **One-click slice to G-code.** Creality Print and OrcaSlicer both have a headless CLI
   (`--slice 0 --outputdir`; see the [Orca CLI](https://www.orcaslicer.com/wiki/cli/cli_mode)).
   Pick a printer/filament profile once, then Send produces G-code directly. It closes the
   "part in minutes" loop.
2. **PDF drawing sheets.** FreeCAD TechDraw headless: an A4/A3 template with a real title block,
   tolerance block and notes.
3. **Print-orientation helper.** Suggest the flat face down, flag overhangs over 45°, and export STLs
   pre-rotated, so lids and covers print face-down without manual fixing in the slicer.
4. **Tolerance presets per printer/material.** Press-fit, slip-fit and snap-fit clearances as named
   facts. The AI uses them and they show on the drawing.
5. **Hole callouts and a thread/insert library.** M2–M8 clearance, tap, and heat-set insert sizes,
   so research isn't needed for standard hardware.
6. **Assemblies.** Several parts with mates and interference checks and hinge/lid swing
   checks, with each part keeping its own drawing.
7. **Packaging.** A PyInstaller or Nuitka single-file .exe and a signed installer, so buyers don't
   need Python. FreeCAD and Ollama can be detected and linked for download on first run.

## Before selling

- [ ] Choose a license, and check dependencies: FreeCAD is LGPL and invoked as a separate process;
      Ollama is MIT; Python is PSF.
- [ ] Name/trademark check for "PartForge" (other products use similar names).
- [ ] Code-signing certificate so Windows SmartScreen doesn't warn on install.
- [ ] Crash reporting (opt-in) and an update check.
- [ ] Test on a clean Windows machine with no FreeCAD, no Ollama and a non-admin user.
- [ ] Try the first-design prompts on the models buyers will actually run (qwen2.5-coder 7B/14B)
      and tune the templates and system prompt to them.
