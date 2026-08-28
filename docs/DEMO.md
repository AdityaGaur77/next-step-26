# Demo script (5-minute video shot list)

## 0:00–0:40 — the problem
- Open OrcaSlicer with a shelf bracket STL.
- "The slicer knows this is 100×10×10 mm. It has no idea it's holding 8 kg."
- Show default slicing; point at uniform walls everywhere.

## 0:40–1:20 — describe the job, not the geometry
- Type into the EcoSlice box: *"shelf bracket holding 8 kg, load downward at the front edge;
  screwed onto left wall"* (or show the JSON load case if UI integration lands later).
- Show the structured load case (forces/constraints/safety factor) — mention Claude parses it
  when an API key is present; deterministic parser otherwise.

## 1:20–2:20 — real FEA inside the slicer
- Console log: voxelization → FEM solve (0.05 s on the bracket; seconds on a bulky part) →
  max von Mises vs allowable, and which solver ran.
- `tools/offline_demo.py --resolution 40` on screen as the readable version of the same numbers.
- One line: "Trilinear hexahedra, validated against the closed-form cantilever solution."

## 2:20–3:00 — the mutation (the wow)
- Slice again with the plugin enabled. Preview: extra perimeters + solid infill bloom exactly at
  the fixed root skins; mid-part stays light.
- Before/after preview side-by-side — the 10 seconds that win the pitch.

## 3:00–3:20 — three options, one solve
- `python tools/offline_demo.py --resolution 40 --options`
- Eco / Balanced / Maximum Strength side by side: added grams split into walls vs solid infill,
  grams saved vs blanket-strengthening, added time and kWh, and a strength-confidence score.
- One line: "Same FEM solve, three thresholdings — and the score refuses to say 'high' when the
  part is over its allowable stress."

## 3:20–3:40 — the proof, on screen
- `python tools/offline_demo.py --resolution 80 --html proof.html` and open it.
- Two elevations side by side: the stress field, then what EcoSlice did about it. On the demo
  cantilever the reinforcement lands on the top and bottom fibres near the clamped root and the
  neutral axis is relaxed — textbook bending, straight out of the FEM rather than asserted.
- Hover a cell: coordinates, utilisation, decision. This is the "why here" answer in one image.

## 3:40–4:20 — measured savings
- `python tools/gcode_diff.py baseline.gcode ecoslice.gcode --assert-lighter`
- The `;ECOSLICE` receipt in the G-code: grams added where physics demanded (split into walls and
  solid infill), grams saved vs blanket-strengthening, gCO₂e with cited constants — and beneath
  them the *measured* lines: mass, print time and kWh straight from the slicer's own export footer.

> **Do not skip this shot, and do not fake it.** This round-trip has not been run yet. If the
> optimized slice comes out heavier than baseline, say so on camera and show the receipt's
> modelled-vs-measured split — the honest framing is "strength placed where physics asks, at a
> stated material cost", not an unproven savings number. See *Scope* in the README.

## 4:20–5:00 — scale & close
- Extrapolation slide: "If every desktop print strengthened like this instead of blanketing…"
  (top-100 Printables re-slice = stretch goal).
- Close on theme: *Earth Forward* — every gram intentional.

## Backup if the live slice will not cooperate
The day-1 gate already passed (2026-08-26, nightly 2.5.0-dev — see `docs/SPIKE.md`), so the
fallback is only about the *recording*, not the capability. Record the offline demo end to end
(`tools/offline_demo.py --options --html proof.html`) + the mock-host mutation walkthrough + the test suite run.
Honest label: mutation verified live on a nightly; this footage is the offline driver.
