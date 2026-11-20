# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Andrew Rainsberger and CurioMesh contributors

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "skills" / "curiomesh-retopo" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "curiomesh-agent" / "scripts"))

import cm_agent as cm  # noqa: E402
import curiomesh_fix as fx  # noqa: E402
import curiomesh_process as process  # noqa: E402
import curiomesh_mcp as mcp  # noqa: E402
from scripts.build_release import build_skills  # noqa: E402

MCP = ROOT / "skills" / "curiomesh-agent" / "scripts" / "curiomesh_mcp.py"
COMMANDS = [
    {"name": "session.info", "help": "Version and state", "edits": False, "schema": {}},
    {"name": "use.pick", "help": "Pick the Use", "edits": True, "schema": {"type": "object", "properties": {"use": {"type": "string"}}, "required": ["use"]}},
]


def fake_blender(folder, stop, seen):
    """Plays CurioMesh: answers every inbox message in name order, as the add-on's timer does."""
    inbox, outbox = Path(folder) / "inbox", Path(folder) / "outbox"
    done = set()
    while not stop.is_set():
        for path in sorted(inbox.glob("*.json")):
            if path.name in done:
                continue
            done.add(path.name)
            msg = json.loads(path.read_text(encoding="utf-8"))
            seen.append(msg)
            if msg["command"] == "commands.list":
                body = {"ok": True, "result": {"commands": COMMANDS}, "step": 0}
            elif msg["command"] == "use.pick":
                body = {"ok": True, "result": msg["args"], "step": 1}
            else:
                body = {"ok": False, "error": {"code": "UNKNOWN_COMMAND", "message": msg["command"]}, "step": 0}
            tmp = outbox / (path.stem + ".tmp")
            tmp.write_text(json.dumps(dict(body, id=msg["id"])), encoding="utf-8")
            os.replace(tmp, outbox / path.name)
        time.sleep(0.01)


class AgentSkillsTest(unittest.TestCase):
    def test_static_asset_disposition(self):
        import copy

        report = {"ok": True, "export": {"warnings": []}, "unreal": {"status": "pass", "warnings": []},
                  "repair": {"quality_verdict": {"human_review": False}},
                  "lods": {"requested": True, "status": "pass", "visual_status": "pass"},
                  "collision": {"requested": "auto", "mode": "convex", "status": "pass", "world": "pie"}}
        self.assertEqual(process.disposition(report), ("PASS", []))
        for stage, changes, decision, reason in (
            ("unreal", {"status": "untested"}, "REVIEW", "unreal_untested"),
            ("unreal", {"status": "unavailable"}, "REVIEW", "unreal_unavailable"),
            ("unreal", {"status": "fail", "warnings": ["unreal_missing_BaseColorTexture"]}, "FAIL", "unreal_missing_BaseColorTexture"),
            ("unreal", {"status": "pass", "warnings": ["unreal_uv_layer_missing"]}, "REVIEW", "unreal_uv_layer_missing"),
            ("lods", {"visual_status": "untested"}, "REVIEW", "lod_visual_validation_pending"),
            ("lods", {"status": "fail"}, "FAIL", "lods_validation_failed"),
            ("collision", {"status": "fail"}, "FAIL", "collision_validation_failed"),
            ("collision", {"world": "editor"}, "REVIEW", "collision_pie_untested"),
        ):
            with self.subTest(stage=stage, changes=changes):
                case = copy.deepcopy(report)
                case[stage].update(changes)
                self.assertEqual(process.disposition(case), (decision, [reason]))
        report["collision"] = {"requested": "auto", "mode": "none", "status": "untested"}
        self.assertEqual(process.disposition(report), ("PASS", []))
        self.assertEqual(process.disposition({"ok": False, "error": "import failed"}), ("FAIL", ["import failed"]))

    def test_lod_ratio_validation(self):
        from argparse import ArgumentTypeError

        self.assertEqual(process.parse_ratios("0.5,0.25"), [0.5, 0.25])
        for invalid in (".25,.5", "1,0.2", "0.5,0", "0.5,0.5", "0.5", "nan,.3", "0.8,.5,.25"):
            with self.subTest(ratios=invalid), self.assertRaises(ArgumentTypeError):
                process.parse_ratios(invalid)

    def test_report_helpers(self):
        self.assertEqual(cm.parse_use("low-poly"), "LOW_POLY")
        self.assertEqual(cm.parse_reduce("25%"), ("PERCENT", 25.0))
        self.assertEqual(cm.parse_faces("1,5,10-12"), [1, 5, 10, 11, 12])
        record = {"totals": {"inward": 3, "pole": 9}, "spots": [{"part": 2, "kinds": {"inward": 3}, "faces": [1], "face_count": 3, "score": 5.0}]}
        summary = cm.issues_summary(record)
        self.assertEqual(summary["per_part"], {"2": {"inward": 3}})
        self.assertEqual(cm.defect_faces(summary), 3)
        good = {"faces": 1000, "detail": 6, "view_changed_pct": 5.9, "tris": 0, "issues": {"totals": {}}}
        self.assertTrue(cm.verdict(good)["ok"])
        bad = dict(good, view_changed_pct=14.0, issues=summary)
        self.assertFalse(cm.verdict(bad)["ok"])
        self.assertTrue(cm.better({"bad": 5}, {"bad": 4}))
        self.assertFalse(cm.better({"bad": 5}, {"bad": 5}))  # equal is not better: the step is undone

    def test_skill_files_and_zip(self):
        skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertGreaterEqual(len(skills), 5)
        for path in skills:
            head = path.read_text(encoding="utf-8").split("---")[1]
            name = re.search(r"^name: (.+)$", head, re.M).group(1)
            self.assertEqual(name, path.parent.name)
            self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertIn("description:", head)
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(build_skills(ROOT, Path(tmp))) as archive:
                names = archive.namelist()
        self.assertIn("curiomesh-retopo/SKILL.md", names)
        self.assertIn("curiomesh-retopo/scripts/curiomesh_run.py", names)
        self.assertIn("curiomesh-retopo/scripts/curiomesh_process.py", names)
        self.assertIn("curiomesh-retopo/scripts/cm_quality.py", names)
        self.assertIn("curiomesh-agent/SKILL.md", names)
        self.assertIn("curiomesh-agent/scripts/curiomesh_mcp.py", names)
        self.assertFalse([n for n in names if "__pycache__" in n])

    def test_no_arbitrary_caps_in_skills(self):
        fix = (ROOT / "skills" / "curiomesh-fix-bad-faces" / "SKILL.md").read_text(encoding="utf-8")
        retopo = (ROOT / "skills" / "curiomesh-retopo" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("4 rounds", fix)
        self.assertNotIn("at most three retries", retopo)
        self.assertIn("a round gains nothing", fix)

    def test_glb_validation_and_invalid_buffer(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "example.glb"
            document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 40}],
                        "bufferViews": [{"buffer": 0, "byteLength": 36},
                                        {"buffer": 0, "byteOffset": 36, "byteLength": 4}],
                        "accessors": [{"bufferView": 0, "count": 3}],
                        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
                        "images": [{"bufferView": 1, "mimeType": "image/png"}]}

            def write():
                manifest = json.dumps(document).encode()
                manifest += b" " * (-len(manifest) % 4)
                chunks = struct.pack("<I4s", len(manifest), b"JSON") + manifest
                chunks += struct.pack("<I4s", 40, b"BIN\0") + bytes(40)
                path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks)

            write()
            self.assertEqual(process.glb_summary(path)["triangles"], 1)
            primitive = document["meshes"][0]["primitives"][0]
            document["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
            document["textures"] = [{"source": 0}]
            primitive["material"] = 0
            write()
            with self.assertRaisesRegex(ValueError, "texture or UV assignment"):
                process.glb_summary(path)
            primitive["attributes"]["TEXCOORD_0"] = 0
            write()
            self.assertEqual(process.glb_summary(path)["pbr_texture_roles"], ["baseColorTexture"])
            primitive["material"] = 1
            write()
            with self.assertRaisesRegex(ValueError, "missing material"):
                process.glb_summary(path)
            primitive["material"] = 0
            document["bufferViews"][1]["byteLength"] = 8
            write()
            with self.assertRaisesRegex(ValueError, "buffer view"):
                process.glb_summary(path)

    def test_unreal_unavailable_and_import_failure(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            helper, mesh = root / "helper.py", root / "asset.glb"
            mesh.write_bytes(b"accepted asset")
            helper.write_text("raise SystemExit(1)\n")
            with self.assertRaises(process.UnrealUnavailable):
                process.unreal_import(helper, mesh, "Unavailable")
            helper.write_text("""import json, sys
if sys.argv[2] == 'list_toolsets':
    text = '- project.scripts.unreal.CurioMeshValidation: Test toolset'
else:
    text = 'The import produced no static mesh'
print(json.dumps({'result': {'content': [{'text': text}], 'isError': sys.argv[2] != 'list_toolsets'}}))
""")
            with self.assertRaisesRegex(RuntimeError, "no static mesh"):
                process.unreal_import(helper, mesh, "FailedImport")
            self.assertEqual(mesh.read_bytes(), b"accepted asset")

    def test_blender_mcp_skill_uses_agentcmd(self):
        text = (ROOT / "skills" / "curiomesh-blender-mcp" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('.agentcmd', text)
        self.assertIn("mod.run(command, args)", text)
        self.assertNotIn("guided._collect_work", text)

    def test_docs_have_commands_and_codes(self):
        agents = (ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8")
        for code in ("BAD_ARGS", "UNKNOWN_COMMAND", "NO_OBJECT", "NO_RESULT", "BUSY", "NOT_READY", "NOT_FOUND", "REFUSED", "FILE", "VERSION", "MISMATCH",
                     "BORDER_MOVED", "HOLE", "SHAPE_LOST", "VIEW_CHANGED", "NEEDS_WINDOW", "INTERNAL"):
            self.assertIn(f"`{code}`", agents)
        parts = (ROOT / "docs" / "PARTS_EXCHANGE.md").read_text(encoding="utf-8")
        self.assertIn("curiomesh-parts", parts)
        # nothing private in the public docs and skills
        for path in [ROOT / "docs" / "AGENTS.md", ROOT / "docs" / "PARTS_EXCHANGE.md", *(ROOT / "skills").rglob("*.md"), MCP]:
            self.assertNotRegex(path.read_text(encoding="utf-8").lower(), r"wreck|bench[/\\]|d:[/\\]", str(path))

    def test_mcp_server_against_fake_mailbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop, seen = threading.Event(), []
            thread = threading.Thread(target=fake_blender, args=(tmp, stop, seen), daemon=True)
            thread.start()
            try:
                server = mcp.Server(mcp.Mailbox(tmp), start_wait=10)
                init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                self.assertEqual(init["result"]["serverInfo"]["name"], "curiomesh")
                self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
                tools = {t["name"]: t for t in server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
                self.assertEqual(set(tools), {"curiomesh_call", "session_info", "use_pick"})
                self.assertIn("timeout", tools["use_pick"]["inputSchema"]["properties"])
                self.assertIn("(changes the result", tools["use_pick"]["description"])
                call = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "use_pick", "arguments": {"use": "GAME", "object": "Crate"}}})
                self.assertFalse(call["result"]["isError"])
                self.assertEqual(seen[-1]["command"], "use.pick")
                self.assertEqual(seen[-1]["args"], {"use": "GAME", "object": "Crate"})
                bad = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "curiomesh_call", "arguments": {"command": "no.such"}}})
                self.assertTrue(bad["result"]["isError"])
                self.assertIn("UNKNOWN_COMMAND", bad["result"]["content"][0]["text"])
                self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"], -32601)
                self.assertFalse(list((Path(tmp) / "inbox").glob("*.tmp")))  # messages appear whole, never as .tmp leftovers
            finally:
                stop.set()
                thread.join(2)

    def test_mcp_timeout_is_the_callers(self):
        with tempfile.TemporaryDirectory() as tmp:  # nobody answers
            with self.assertRaises(mcp.MailboxError):
                mcp.Mailbox(tmp).call("session.info", timeout=0.2)

    def test_mcp_stdio_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop, seen = threading.Event(), []
            thread = threading.Thread(target=fake_blender, args=(tmp, stop, seen), daemon=True)
            thread.start()
            try:
                lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                         {"jsonrpc": "2.0", "method": "notifications/initialized"},
                         {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
                run = subprocess.run([sys.executable, "-I", str(MCP), "--folder", tmp], input="\n".join(json.dumps(m) for m in lines) + "\n",
                                     capture_output=True, text=True, timeout=30)
            finally:
                stop.set()
                thread.join(2)
        replies = [json.loads(x) for x in run.stdout.strip().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertIn("session_info", [t["name"] for t in replies[1]["result"]["tools"]])

    def test_fix_rounds_stop_when_a_round_gains_nothing(self):
        def nothing(key, current, fresh):
            return {"fix": key, "kept": False}, None

        start = {"bad": 5}
        cur, steps, rounds = fx.fix_rounds(["A", "B"], start, nothing)
        self.assertEqual((rounds, len(steps), cur), (1, 2, start))  # one empty round, then stop

        state = {"n": 0}

        def three_gains(key, current, fresh):
            state["n"] += 1
            if state["n"] <= 3:
                return {"fix": key, "kept": True}, {"bad": current["bad"] - 1}
            return {"fix": key, "kept": False}, None

        cur, steps, rounds = fx.fix_rounds(["A"], {"bad": 5}, three_gains)
        self.assertEqual((rounds, cur["bad"]), (4, 2))
        self.assertEqual([s["round"] for s in steps], [1, 2, 3, 4])

        def always(key, current, fresh):
            return {"fix": key, "kept": True}, {"bad": current["bad"] - 1}

        cur, _, rounds = fx.fix_rounds(["A"], {"bad": 50}, always)  # more rounds than the old cap of 4
        self.assertEqual((rounds, cur["bad"]), (50, 0))
        self.assertEqual(fx.fix_rounds([], {"bad": 3}, always)[2], 0)


if __name__ == "__main__":
    unittest.main()
