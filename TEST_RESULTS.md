# CurioMesh-BUH 0.9.9 test results

Tests used Linux x86-64, Blender 5.3.0 build `02f0899d78e3`, and Blender's Python 3.13.13. I copied the stock CurioMesh extension and automation wrappers into isolated test directories. The Linux Turbo binary matched the upstream release ZIP, which passed the published SHA-256 check. All 201 non-cache files of the working installation matched their before-test SHA-256 hashes after testing.

## Retopology baseline

The stock skill processed the same 40,000-triangle, 725-component ComfyUI asset using GAME in each run. These headless results reproduce the saved report. A separate graphical Balanced run using the upstream extension installed from disk in a virtual X display also produced 5,809 quads and 5.32% view change. [Capability-test fixtures and report](https://github.com/HelHeresyDev/BeneathUncountedHeavens/tree/39eec99/blender-tests/curiomesh_capability_test)

| Draft | Detail | Quads | GLB triangles | CurioMesh view changed |
| --- | ---: | ---: | ---: | ---: |
| High Fidelity | 2 | 16,116 | 32,232 | 2.17% |
| Balanced | 5 | 5,809 | 11,618 | 5.32% |
| Aggressive | 10 | 2,304 | 4,608 | 10.13% |

With the old helper, the original-plus-`_Retopo` High Fidelity file failed with `ReferenceError: StructRNA of type Object has been removed`. Blender had consumed the other joined meshes and the helper still read their object names. The new Blender regression checks the original-plus-result case, exact names, Blender's numbered duplicates, preserved world transforms and existing scene objects, generic joins, safe rejection, and retry. The file-to-file repair CLI also processed both original-plus-result capability-test files directly.

The Python suite ran 833 cases with 108 skips. A separate run with the verified Linux binary loaded ran 29 native test cases with one skip. A CLI request for a missing object returned a structured failure and created no geometry output.

## Controlled repair comparison

The post-apply Fix Selection scan compares the applied mesh against its original GLB. The initial retopology reports score preview parts, so their inward, folded and overlap figures can differ from the repair scan. The figures below use the same Fix Selection measurement before and after each repair.

| Draft | Defects before → after | Quads before → after | Open edges before → after | Components | Reimported GLB triangles |
| --- | --- | ---: | ---: | ---: | ---: |
| Balanced | 5 folded → 1 bent | 5,809 → 5,806 | 18 → 18 | 5 | 11,612 |
| High Fidelity | 12 folded → 3 bent | 16,116 → 16,107 | 18 → 22 | 5 | 32,214 |

On Balanced, `UNTANGLE,RELAX,PATCH` matched `ALL`. Untangle and Relax alone gained nothing. Patch rebuilt the affected region and reduced the total by three quads. A second attempt on the remaining face with `RELAX,PATCH --faces 3982 --grow 2` rolled back both steps. High Fidelity matched the earlier 12-to-3 reduction, with four extra open edges after repair.

Balanced had no geometric degenerates before or after at the fixture's area threshold of `1e-8`, and had one effectively zero-signed-area UV polygon before and after at the comparison threshold of `1e-10` double area. Its maximum sampled distance to the source surface was 0.011583 Blender units both times. The 95th percentile moved from 0.001740 to 0.001740 at six decimal places. A six-axis 256-pixel comparison of the outline and depth against the source, with a 0.01 Blender-unit depth tolerance, measured 1.699% before and after. This uses CurioMesh's `triadq.primitives.view_miss` and is a separate measure from the retopology report's view-change figure. The front, top, and three-quarter Workbench renders showed the same silhouette at render scale. Both repaired GLBs reimported with one material, a UV channel, and two embedded 1024×1024 PNGs with the same bytes as the input. GLB triangle and component counts reflect triangulation and attribute splits at seams. Blender mesh open-edge counts are taken on the quad meshes before export.

The defect IDs are on the repaired quad mesh. Balanced's remaining bent face is `3982` at approximately `(0.377790, 0.469611, 0.267214)`. High Fidelity's are `16103`, `16104`, and `16105`. Those three persisted after a second accepted repair removed an overlap, and the next `ALL` step gained nothing. The JSON report includes the center and kind for each remaining face.

## Native package and remaining validation

The isolated 5.3 extension ZIP installed, enabled, ran the Analyze operator and loaded `_curiomesh_turbo.abi3.so` version 0.1.0 from its own extension directory. Native acceleration also loaded in the graphical upstream test. The reported native policy warning did not appear on either local install. The upstream Blender Extensions website build is Python-only under the site's policy, while the GitHub ZIP is the locally installed native build. The 5.3 ZIP retains the upstream native loader and needs no global security changes. [Upstream release](https://github.com/CurioCrafter/CurioMesh/releases/tag/v0.9.9) · [Blender Extensions policy](https://extensions.blender.org/terms-of-service/)

Unreal import, LOD import, and texture appearance in Unreal are unvalidated. The next priorities are geometry-aware acceptance checks for repair candidates, followed by an isolated Unreal import comparison using the same Balanced mesh across export formats.
