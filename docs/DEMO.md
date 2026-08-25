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
- Console log: voxelization → FEM solve time (~0.3 s) → max von Mises vs allowable.
- `tools/offline_demo.py --resolution 64` on screen as the readable version of the same numbers.
- One line: "Trilinear hexahedra, validated against the closed-form cantilever solution."

## 2:20–3:20 — the mutation (the wow)
- Slice again with the plugin enabled. Preview: extra perimeters + solid infill bloom exactly at
  the fixed root skins; mid-part stays light.
- Before/after preview side-by-side — the 10 seconds that win the pitch.

## 3:20–4:10 — measured savings
- `python tools/gcode_diff.py baseline.gcode ecoslice.gcode --assert-lighter`
- The `;ECOSLICE` receipt in the G-code: grams added where physics demanded,
  grams saved vs blanket-strengthening, gCO₂e with cited constants.

## 4:10–5:00 — scale & close
- Extrapolation slide: "If every desktop print strengthened like this instead of blanketing…"
  (top-100 Printables re-slice = stretch goal).
- Close on theme: *Earth Forward* — every gram intentional.

## Backup if the nightly gate slips
Record the offline demo end-to-end (`tools/offline_demo.py`) + mock-host mutation walkthrough +
test suite run. Honest label: slicer-integration pending day-1 gate; all analysis verified.
