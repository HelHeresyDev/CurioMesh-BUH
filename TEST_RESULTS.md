# CurioMesh-BUH test results

## Milestone 1 baseline

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

Unreal import and rendering were outside the milestone 1 test. The milestone 2 results follow.

## Milestone 2 repair results

The pre-gate CLI reproduced both milestone 1 defect counts in a separate Blender 5.3 profile. The new gate then used the same applied quad fixtures and original textured GLB.

| Fixture | Bad faces before → retained | Quads before → retained | Open edges before → retained | Repair decision |
| --- | --- | --- | --- | --- |
| Balanced | 5 folded → 1 bent | 5,809 → 5,806 | 18 → 18 | `ALL` accepted |
| High Fidelity | 12 folded → 12 folded | 16,116 → 16,116 | 18 → 18 | `ALL` and `PATCH` rejected and rolled back |

For Balanced, the retained result has five components, zero geometric degeneracies, zero crossing pairs, and one existing collapsed UV polygon. The symmetric 95th-percentile and maximum sampled distances to the original stayed at 0.002426 and 0.024955 Blender units. The silhouette mismatch to the original stayed at 0.226%, with no changed pixels between the candidate and the pre-repair result at the configured resolution. The accepted patch trades five folds for one bent face. The remaining face is `3982` at approximately `(0.377790, 0.469611, 0.267214)`. Untangle, Relax, and Patch gained nothing at zero and three rings. The opt-in triangulation candidate retained the sampled surface and silhouette but produced two folded faces, so it was rolled back. Human review remains required for the bent face and the collapsed UV polygon.

For High Fidelity, `ALL` and `PATCH` each proposed four residual defects, including one overlap, and raised the open-edge count from 18 to 22. The boundary test identified four segments away from the pre-repair boundary, so the gate retained the original 12 folded faces and all 18 original open edges. Clean, Flip, Untangle, and Relax alone produced no reduction. This preserves the topology while leaving a clear repair target for later work.

The Python suite ran 834 tests with 108 skips. The synthetic Blender test exercised an unchanged intentional opening and multiple components, a split along an existing boundary, an accidental hole, non-manifold and zero-area geometry, UV changes, a material change, a bent quad, and restoring the last mesh after an operator cancelled following a vertex edit. The lantern fixtures exercised the actual folded-face operations and verified acceptance and rejection. The CLI processed the original GLB to 11,612 triangles, checked the GLB against the accepted Blender mesh, and imported it through MCP. Both embedded PNG checksums matched the original. The final JSON marked the asset for human review and recorded no Unreal import comparison warnings. All 200 files bundled in the active Blender extension matched the milestone 1 ZIP after testing.

## Unreal Engine 5.8.4 import and rendered comparison

The test used headless imports and a graphical Vulkan editor after the project's VRAM handoff. Assets are isolated under `/Game/Developer/CurioMeshBUHTests/`. Both meshes imported as one static asset. Their bounds match within floating-point rounding at about 123.63 × 124.91 × 187.21 cm and both preview pivots are at the origin. Orientation and major features matched the Blender references in all three tested directions. An isolated PIE simulation started and the GLB static mesh actor was present in the PIE world.

| Import | Source triangles | Reported LOD0 render triangles | LOD0 render vertices | UV channels | Simple collision |
| --- | ---: | ---: | ---: | ---: | ---: |
| GLB | 11,612 | 3,752 with Nanite enabled | 5,397 | 1 | 0 shapes, Use Default |
| FBX | 11,612 | 11,612 | 12,578 | 1 | 0 shapes, Use Default |

The GLB's Unreal source mesh reported 1,254 groups before a diagnostic weld and five after checking coincident boundaries on a disposable dynamic copy. FBX's source mesh already reported five. The import retained the intended disconnected islands and the diagnostic did not weld the asset itself. The source triangle count is the comparable figure for the Nanite GLB.

GLB created two 1024 × 1024 textures. Interchange bound `Image_0` to Base Color in sRGB and `Image_1` to Metallic Roughness in linear space. Blender and Unreal show the brown paint, roof and window opening. The Unreal preview is darker in shadow and uses a different lighting and tone-mapping setup. FBX imported geometry and UVs, but the automated import returned no textures. Its transient material referenced no textures and had no material assigned after reopening the saved mesh. The FBX captures show the model in gray. GLB is the tested textured path for this fixture. Gameplay collision remains to be authored.

[Blender, Unreal GLB, and Unreal FBX comparison](docs/evidence/milestone-2-imports.png). Rows show those three outputs in that order. Columns are front, side, and three-quarter.

## LOD trials

CurioMesh exported three GLBs from the repaired lantern GLB using `--lods 100,50,25`. Unreal imported each as a separate static mesh with both textures. The actual source triangle counts were **17,266, 11,244, and 6,142**. Their CurioMesh view changes relative to the repaired asset were 2.61%, 5.07%, and 11.52%. All three produced six groups after the diagnostic weld. The CurioMesh LOD0 was heavier than the 11,612-triangle accepted mesh, so these exports were not adopted as a combined runtime chain.

The separate Unreal-generated FBX variant had **11,612, 5,806, and 2,902** render triangles at its three LODs. At approximately 3.5, 5.8, and 8.4 m, the forced LOD renders retained a recognizable silhouette. LOD1 and LOD2 had no UV channels reported by the editor query. The shared GLB material was assigned in metadata, but these captures stayed gray, so the variant did not pass visual texture verification. [Unreal LOD captures](docs/evidence/milestone-2-unreal-lods.png)

Investigate a textured custom LOD chain using the accepted GLB as LOD0 and verify game collision separately. Keep the unresolved Balanced defect and High Fidelity boundary rejection as regression cases.
