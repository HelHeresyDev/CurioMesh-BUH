---
name: curiomesh-retopo
description: Retopologise a 3D model into a clean all-quad, low-poly mesh with the CurioMesh Blender add-on, headless, from a file (.obj .fbx .glb .blend) to a file. Use when the user wants a high-poly mesh, scan, sculpt or game asset reduced, retopologised, quad-remeshed or made game-ready, and CurioMesh is installed or its folder is at hand.
---

# CurioMesh retopo (headless)

CurioMesh reduces a mesh part by part and checks every result against the original from six sides. `scripts/curiomesh_run.py` drives it with no Blender window and writes a JSON report you read. Nothing is uploaded.

## 1. Find Blender

Use the first that exists, and say which you used:

1. `$BLENDER` (a path to the Blender executable).
2. `blender` on PATH.
3. Windows: `C:\Program Files\Blender Foundation\Blender *\blender.exe` (newest). macOS: `/Applications/Blender.app/Contents/MacOS/Blender`. Linux: `/usr/bin/blender`, `/snap/bin/blender`, `~/blender*/blender`.

Upstream CurioMesh supports Blender 3.6 to 5.2. The BUH development package was tested with Blender 5.3.0. Use its isolated profile or pass the extracted add-on folder.

## 2. Find CurioMesh

- Installed add-on: nothing to pass.
- Repo folder (the one holding `guided.py`): pass `--addon <folder>` or set `CURIOMESH_REPO`.
- If `Report` says "not installed", tell the user to install it from https://github.com/CurioCrafter/CurioMesh/releases/latest (see the README).

## 3. Pick the Use and Detail

Map what the user says; if they say nothing, use `--use AUTO` (CurioMesh looks at the mesh and picks, like its Auto button).

| User wants | `--use` | Detail |
|---|---|---|
| game prop, "game ready", default | `GAME` | 6 |
| fewest faces, background prop | `LOW_POLY` | 10 |
| character or creature that bends | `ANIMATION` | 1.5 |
| subdivision / smooth later | `SUBDIV` | 1.5 |
| noisy scan or photogrammetry | `SCAN` | 2 |
| a base to sculpt on | `SCULPT` | 6 |

Detail is the percent of the view that may differ from the original: lower keeps more faces. If the user names a face count or a share ("about 5,000 faces", "a quarter"), add `--reduce-to 5000` or `--reduce-to 25%`.

## 4. Run

```
"$BLENDER" -b --factory-startup --python <this skill>/scripts/curiomesh_run.py -- IN --use GAME --detail 6 --out OUT.glb --report report.json
```

- `IN`: `.obj .fbx .glb .gltf .blend .ply .stl`. Several meshes in one file are joined. `OUT`: `.obj .fbx .blend .glb`. Use `.obj` or `.blend` if you may fix bad faces afterwards: `.glb` stores triangles only, so a quad mesh re-imported from it has twice the faces.
- Add `--addon <folder>` when running from a repo checkout.
- Run it in the background if your tool times out. A 60,000-face model takes minutes; scans can take tens of minutes. Keep one Blender running at a time on the user's PC.
- Read `report.json` (also printed as one `CURIOMESH_REPORT {...}` line). If `ok` is false, read `error`, fix the cause and rerun; do not retry blindly.

## 5. Read the report

For the BUH pipeline, invoke `python <this skill>/scripts/curiomesh_process.py IN.glb --preset balanced --addon PATH --out OUT.glb --report REPORT.json`. It creates a temporary Blender profile, screens repairs, and validates the GLB. Read `disposition`, `disposition_reasons`, and `human_review` before moving an asset into production. Use new output and report paths on retries.

For an Unreal 5.8.4 prop, add `--asset-category architecture|organic|hard_surface|decorative`, choosing one value, and `--lods --lod-ratios 0.5,0.25`. Pass the local `--unreal-mcp-helper` and a unique `--unreal-run-id`. The editor needs the fork's validation toolset loaded and `CURIOMESH_VALIDATION_EXPORTS` set to the directory containing `OUT.glb`. Add `--collision auto|none|simple|convex|complex`, choosing one mode, and `--collision-probes PROBES.json --unreal-pie` for gameplay queries. Use explicit `complex` only for stationary shapes that need concave collision, and verify the openings with probes. `--preset auto` uses Balanced and flags an unspecified category for review.

Unreal is reported as untested when skipped and unavailable when the validation editor cannot be reached. Inspect textured LOD captures for silhouette and material appearance before clearing `lod_visual_validation_pending`. [Project setup and replay instructions](https://github.com/HelHeresyDev/CurioMesh-BUH/blob/buh/blender-5.3/DEVELOPMENT.md#static-props-from-blender-to-unreal)

Keys: `faces`, `quads`, `tris`, `quad_pct`, `source_faces`, `view_changed_pct`, `seconds`, `parts`, `issues` (`totals` per kind, `spots`, `per_part`, `worst`), `verdict` (`ok` and `problems`).

What is good (from the README's examples, Game Asset at Detail 6):
- `view_changed_pct` at or under the Detail you asked for, up to about 1.5 times it. Examples land at 5.6 to 6.0.
- `quad_pct` near 100 and `tris` under 2% of `faces`.
- `issues.totals` showing no `inward`, `crossing`, `folded_over`, `sunk` and at most a handful of `flat`, `duplicate`, `overlap`. Poles, slivers and pinched faces are untidy, not wrong.
- Face count: the README's examples keep 2% to 20% of the source on hard-surface models.

## 6. Retry when it is not good

Change one thing per retry and keep the best result. Keep retrying while the numbers improve; stop when a retry gains nothing:
- `view_changed_pct` too high (shape lost, thin parts gone): lower Detail (6 to 3, then 1.5). Faces go up.
- Too many faces for the user: raise Detail (6 to 10), or switch to `LOW_POLY`, or use `--reduce-to`.
- Defect kinds in `issues.totals` (inward, folded, crossing, sunk): run the `curiomesh-fix-bad-faces` skill on the output. If one whole part is bad, rerun with `--keep-original-bad`.
- Organic model with ugly flow: try `--use ANIMATION` or `SCULPT`.
- Run fails or hangs on a huge mesh: set `CURIOMESH_HELPERS=2`, or decimate nothing and ask the user.

## 7. Report back

Tell the user: output path, faces before and after, quads %, view changed %, the issue totals, and what you changed on any retry. Say plainly if it still has bad faces.

## Honest limits

Quads can look slanted on smooth surfaces. Fine ornaments go jagged at high reduction. It does not place edge loops for joints on its own. Baking (`--bake normal`, `--bake-size 2048`) runs Cycles inside Blender and can take minutes.
