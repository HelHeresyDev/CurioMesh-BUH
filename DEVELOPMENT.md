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
