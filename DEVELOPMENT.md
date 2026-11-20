# CurioMesh-BUH development

The `buh/blender-5.3` branch starts at upstream tag `v0.9.9`, commit `bf0d12ef901238853350f8de2ed425f0f6f2fedd`. Use `origin` for your fork and `upstream` for `https://github.com/CurioCrafter/CurioMesh.git`. Fetch releases with `git fetch upstream --tags`. Inspect the diff from the tag pinned by this branch, test the next upstream release on a new branch, then merge verified updates into the development branch.

The five agent skills are in `skills/`. Use the fork's `curiomesh-retopo` scripts and the updated `curiomesh-fix-bad-faces` instructions together. The `curiomesh-agent`, `curiomesh-batch-and-lods`, and `curiomesh-blender-mcp` skills retain their upstream interfaces. They can use the fork in an isolated Blender process. Keep the normal profile and its globally installed OpenCode skills available for stock runs.

## Build and test on Linux x86-64

These commands run from the fork checkout. For the first development build, reuse upstream's verified Linux binary. `native/` holds the Rust source, Cargo lockfile and build metadata. To rebuild from source, use `cargo build --locked --release --manifest-path native/Cargo.toml` and put the resulting `native/target/release/lib_curiomesh_turbo.so` at `linux_x86_64/_curiomesh_turbo.abi3.so` inside your build's native staging directory. A build with `--native-dir` omitted uses the Python paths.

```sh
stage="$(mktemp -d)"
gh release download v0.9.9 -R CurioCrafter/CurioMesh \
  --pattern curiomesh-0.9.9-blender-5.2-extension.zip --pattern SHA256SUMS.txt --dir "$stage"
(cd "$stage" && awk '/curiomesh-0.9.9-blender-5.2-extension.zip$/ {print}' SHA256SUMS.txt | sha256sum -c -)
mkdir -p "$stage/native/linux_x86_64"
unzip -p "$stage/curiomesh-0.9.9-blender-5.2-extension.zip" _native/linux_x86_64/_curiomesh_turbo.abi3.so \
  > "$stage/native/linux_x86_64/_curiomesh_turbo.abi3.so"
python3 scripts/build_release.py --per-version --native-dir "$stage/native"
```

`dist/curiomesh-0.9.9-blender-5.3-extension.zip` is the development extension. `dist/curiomesh-0.9.9-agent-skills.zip` holds the wrappers and skill files. Set the profile variables below in a dedicated shell before installing the extension from disk or running tests. The install check uses Blender's own extension installer.

```sh
profile="$(mktemp -d)"
export BLENDER_USER_CONFIG="$profile/config"
export BLENDER_USER_EXTENSIONS="$profile/extensions"
export BLENDER_USER_SCRIPTS="$profile/scripts"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS" "$BLENDER_USER_SCRIPTS"
blender -b --factory-startup --python-exit-code 1 --python tests/blender_package_smoke.py -- \
  --layout extension dist/curiomesh-0.9.9-blender-5.3-extension.zip
blender -b --factory-startup --python-exit-code 1 --python tests/blender_agent_load_mesh.py
python3 -m unittest discover -s tests -p 'test_*.py'
```

You can extract the development ZIP into a directory named `curiomesh` and pass that directory as `--addon` for a file-to-file run. In a fresh profile that already has the development extension enabled, the scripts can find it without `--addon`. The fork and stock build share the extension ID, so use one in each profile.

## Repeat the fixture runs

Set `fixture` to the [`blender-tests/curiomesh_capability_test`](https://github.com/HelHeresyDev/BeneathUncountedHeavens/tree/39eec99/blender-tests/curiomesh_capability_test) directory in your local project checkout. Set `addon` to the extracted development package and keep using the isolated profile. The `--original-object` flag allows the source and retopo mesh to come from the same `.blend` when you know both object names.

```sh
scratch="$(mktemp -d)"
source_glb="$fixture/input/triposg_painted_00001_.glb"
for detail in 2 5 10; do
  blender -b --factory-startup -t 6 --python-exit-code 1 \
    --python skills/curiomesh-retopo/scripts/curiomesh_run.py -- "$source_glb" --addon "$addon" \
    --use GAME --detail "$detail" --out "$scratch/detail-$detail.blend" --report "$scratch/detail-$detail.json"
done
for case in balanced high_fidelity; do
  blender -b --factory-startup -t 4 --python-exit-code 1 \
    --python skills/curiomesh-retopo/scripts/curiomesh_fix.py -- "$fixture/output/$case.blend" --addon "$addon" \
    --original "$source_glb" --fix ALL --out "$scratch/$case.glb" --report "$scratch/$case-repair.json"
done
```

Use `.blend` to inspect or continue repairing quads. Use `--object Result` on a repair-generated `.blend` if the scene also contains the reference. The existing fixture measurement and comparison render scripts are in `$fixture/scripts`.

## Repair acceptance

Run the synthetic Blender regression against an extracted development package. Use the lantern commands above to test the real fixture.

```sh
CURIOMESH_TEST_ADDON="$addon" blender -b --factory-startup --python-exit-code 1 \
  --python tests/blender_repair_quality.py
```

Each repair runs on a copy of the last accepted mesh datablock. The prior datablock is restored after a rejection or a cancelled operation, including topology, UVs and assignments. The report retains the rejected measurements. A candidate must reduce the total defect count. New degenerate faces, non-manifold or loose edges, crossings, overlapping faces, and unintended open boundaries are hard failures. Boundary edges on the same existing path may be subdivided. A new bend or triangle is a warning if the other checks pass. Existing openings and disconnected islands are assessed relative to the previous mesh.

Surface distances use the greater of the two directions' 95th percentile and sampled maximum. Each direction checks up to 8,192 ordered vertices and 8,192 triangle centers. Silhouette mismatch uses six orthographic 128-pixel masks in a fixed frame and expresses the changed pixels as a share of their union. Surface and bounds changes are normalized by the source/result bounding-box diagonal. The gate compares to both the previous accepted mesh and the initial result to limit cumulative drift. With no original file, measurements use the initial result and the report asks for human review. Intersections reuse CurioMesh's non-neighbor face-pair detector. Boundary matching samples endpoints and midpoints using nearby previous segments. These are sampled checks, with finite resolution on small details.

Supply `--quality-policy thresholds.json` to override individual numeric defaults.

| Field | Default | Meaning |
| --- | ---: | --- |
| `boundary_tolerance_ratio` | 0.0001 | Distance allowed between a new boundary segment sample and an existing boundary |
| `surface_p95_growth_ratio` | 0.0005 | Increase in the sampled 95th-percentile distance |
| `surface_max_growth_ratio` | 0.002 | Increase in the maximum sampled distance |
| `silhouette_growth_pct` | 0.15 | Increase in source-relative silhouette mismatch in percentage points |
| `silhouette_step_pct` | 0.35 | Silhouette mismatch allowed between the previous and proposed meshes, in percent |
| `bounds_shift_ratio` | 0.002 | Largest absolute change to a bounding-box coordinate |
| `allowed_component_change` | 0 | Maximum absolute change in connected component count |

The script compares the UV layer names, per-corner finiteness, collapsed polygons, untouched face UVs, material slots, image bindings, and face material assignments. Zero-area geometry is measured relative to the mesh diagonal squared. `quality_verdict.human_review` remains true when the accepted mesh still has a geometry or UV issue, or an accepted candidate incurred a warning.

## Processing and Unreal validation

The wrapper writes a GLB and JSON report to new paths. It uses a temporary Blender profile, checks the exported GLB's container, buffers, triangle primitives, materials and image payloads, and reports whether review is required.

```sh
python3 skills/curiomesh-retopo/scripts/curiomesh_process.py "$source_glb" \
  --preset balanced --addon "$addon" --out "$scratch/Lantern.glb" --report "$scratch/pipeline.json"
```

For Unreal work in Beneath Uncounted Heavens, set `CURIOMESH_VALIDATION_EXPORTS` to the absolute directory holding the outputs. Launch Unreal Editor 5.8.4 with the project path, `-ModelContextProtocolStartServer -ModelContextProtocolPort=8191 -EnablePlugins=PythonScriptPlugin,GeometryScripting`, and `-ExecutePythonScript=ABSOLUTE_PATH_TO/scripts/unreal/curiomesh_validation_toolset.py`. Use `UnrealEditor-Cmd -nullrhi -unattended` for the headless import. The startup script registers tools, and the imports and preview changes run through MCP. Use the XWayland editor wrapper and the project's VRAM handoff for real renders. The validation tools restrict writes to `/Game/Developer/CurioMeshBUHTests/<run_id>/` and render target outputs to the configured directory.

Run the processing command with `MCP_URL=http://127.0.0.1:8191/mcp`, `--unreal-mcp-helper /path/to/BeneathUncountedHeav/tmp_opencode/mcp_do.py`, and `--unreal-run-id UniqueName` for optional import. The GLB output directory must be the validation export directory. To validate prebuilt GLB or FBX files, discover the toolset with the helper's `- list_toolsets -` call, then invoke `import_mesh` with a JSON file containing `run_id` and `filename`. `inspect_mesh` takes `run_id` and `mesh_path` and reports source and render mesh data separately. For an imported mesh, `prepare_preview` takes the same arguments. On the resulting level, invoke `capture_preview` with `run_id` and `forced_lod` (`-1` for normal rendering). The outputs are in `preview_renders`.

```sh
blender -b --factory-startup --python-exit-code 1 \
  --python scripts/unreal/render_blender_reference.py -- "$scratch/Lantern.glb" "$scratch/reference-views"
```
