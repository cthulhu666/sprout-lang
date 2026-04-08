from __future__ import annotations

import unittest

from tests.codegen_test_support import *


class CodegenNativeAnalysisTests(CodegenTestCase):
    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_service_builtin_is_internal_to_public_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_type_of("1") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(run.returncode, 0)
            output = run.stdout + run.stderr
            self.assertIn("repl_type_of", output)
            self.assertIn("internal", output)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_complete_builtin_is_internal_to_public_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_complete("str") with
                  | (_, _) -> print("ok")
                """,
                encoding="utf-8",
            )
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(run.returncode, 0)
            output = run.stdout + run.stderr
            self.assertIn("repl_complete", output)
            self.assertIn("internal", output)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_complete_in_state_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                import stdlib.collections (vec_empty, vec_append)

                fn first_or(lines: Vec String, fallback: String) -> String =
                  match lines with
                  | Vec raw ->
                      match vec_get(0, Vec(raw)) with
                      | Just first -> first
                      | Nothing -> fallback

                fn singleton(x: String) -> Vec String =
                  vec_append(x, vec_empty())

                fn main() -> Unit !{IO} =
                  match compiler.complete_in_state("fr", singleton("import stdlib.bytes (from_string)"), singleton("let answer = 41")) with
                  | (prefix, matches) -> print(prefix ++ ":" ++ first_or(matches, "<empty>"))
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "fr:from_string")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_type_of_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.type_of_in_source("module app.repl", "1") with
                  | Ok inferred -> print(inferred)
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "Int")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_type_of_in_source_builtin_surfaces_service_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.type_of_in_source("module app.repl", "missing") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertIn("Unknown variable missing", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_check_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.check_source("module app.repl\n\nlet local = 41") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "ok")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_bridge_reuses_one_analysis_service_process_per_run(self) -> None:
        src = """
        module main
        import stdlib.compiler as compiler

        fn main() -> Unit !{IO} =
          match compiler.check_source("module app.repl") with
          | Err message -> print(message)
          | Ok _ ->
              match compiler.check_source("module app.repl\n\nlet local = 41") with
              | Err message -> print(message)
              | Ok _ -> print("ok")
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "service.log"
            script_path = tmp_path / "service.py"
            script_path.write_text(
                f"""
import json
import sys

with open({str(log_path)!r}, "a", encoding="utf-8") as log:
    log.write("started\\n")
for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    if request.get("op") != "check_source":
        print(json.dumps({{"ok": False, "error": "unexpected op"}}), flush=True)
        continue
    print(json.dumps({{"ok": True, "value": None}}), flush=True)
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with compiled_native_binary(self, src) as bin_path:
                env = dict(os.environ)
                env["SPROUT_ANALYSIS_SERVICE_CMD"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(script_path))}"
                run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "ok")
            self.assertEqual(log_path.read_text(encoding="utf-8").splitlines(), ["started"])

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_bridge_restarts_service_once_after_mid_run_exit(self) -> None:
        src = """
        module main
        import stdlib.compiler as compiler

        fn main() -> Unit !{IO} =
          match compiler.check_source("module app.repl") with
          | Err message -> print(message)
          | Ok _ ->
              match compiler.check_source("module app.repl\n\nlet local = 41") with
              | Err message -> print(message)
              | Ok _ -> print("ok")
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "service.log"
            state_path = tmp_path / "state.txt"
            script_path = tmp_path / "service.py"
            script_path.write_text(
                f"""
import json
import sys
from pathlib import Path

log_path = Path({str(log_path)!r})
state_path = Path({str(state_path)!r})
with log_path.open("a", encoding="utf-8") as log:
    log.write("started\\n")
served = int(state_path.read_text(encoding="utf-8")) if state_path.exists() else 0
for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    if request.get("op") != "check_source":
        print(json.dumps({{"ok": False, "error": "unexpected op"}}), flush=True)
        continue
    print(json.dumps({{"ok": True, "value": None}}), flush=True)
    served += 1
    state_path.write_text(str(served), encoding="utf-8")
    if served == 1:
        sys.exit(0)
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with compiled_native_binary(self, src) as bin_path:
                env = dict(os.environ)
                env["SPROUT_ANALYSIS_SERVICE_CMD"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(script_path))}"
                run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "ok")
            self.assertEqual(log_path.read_text(encoding="utf-8").splitlines(), ["started", "started"])

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_eval_bridge_does_not_retry_after_transport_loss(self) -> None:
        src = """
        module main
        import stdlib.compiler as compiler

        fn main() -> Unit !{IO} =
          match compiler.eval_lines_in_source("module app.repl\n\nlet local = 41", "local + 1") with
          | Ok lines -> print("ok")
          | Err message -> print(message)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "service.log"
            script_path = tmp_path / "service.py"
            script_path.write_text(
                f"""
import json
import sys

with open({str(log_path)!r}, "a", encoding="utf-8") as log:
    log.write("started\\n")
for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    if request.get("op") == "eval_expr_in_source":
        sys.exit(0)
    print(json.dumps({{"ok": False, "error": "unexpected op"}}), flush=True)
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with compiled_native_binary(self, src) as bin_path:
                env = dict(os.environ)
                env["SPROUT_ANALYSIS_SERVICE_CMD"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(script_path))}"
                run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stderr, "")
            self.assertIn("analysis service: empty response", run.stdout)
            self.assertEqual(log_path.read_text(encoding="utf-8").splitlines(), ["started"])

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_check_source_builtin_reports_bad_service_command_clearly(self) -> None:
        src = """
        module main
        import stdlib.compiler as compiler

        fn main() -> Unit !{IO} =
          match compiler.check_source("module app.repl\n\nlet broken = missing") with
          | Ok _ -> print("ok")
          | Err message -> print(message)
        """
        with compiled_native_binary(self, src) as bin_path:
            env = dict(os.environ)
            env["SPROUT_ANALYSIS_SERVICE_CMD"] = "sprout-missing-analysis-service-command"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("analysis service: command failed to start", run.stdout)
        self.assertIn("SPROUT_ANALYSIS_SERVICE_CMD", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_check_source_builtin_surfaces_service_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.check_source("module app.repl\n\nlet broken = missing") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertIn("Unknown variable missing", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_declared_names_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn contains(target: String, lines: Vec String) -> Bool =
                  match lines with
                  | Vec raw ->
                      match vec_get(0, Vec(raw)) with
                      | Just first -> if first == target then true else contains(target, vec_slice(1, vec_length(Vec(raw)) - 1, Vec(raw)))
                      | Nothing -> false

                fn main() -> Unit !{IO} =
                  match compiler.declared_names_in_source("module app.repl\n\nlet zebra = 1\nlet apple = 2") with
                  | Ok names -> print(if contains("apple", names) then "ok" else "missing")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=self._native_analysis_service_env())
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "ok")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_exported_names_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.exported_names_in_source("module app.lib\n\nlet banana = 1") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=self._native_analysis_service_env())
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "ok")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_symbol_inventory_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.symbol_inventory_in_source("module app.lib\nimport stdlib.bytes (from_string)\nlet apple = from_string(\\\"x\\\")") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=self._native_analysis_service_env())
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "ok")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_analysis_symbol_inventory_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.symbol_inventory_in_source("module app.lib\nimport stdlib.bytes (from_string)\nlet apple = from_string(\\\"x\\\")") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=self._native_analysis_service_env())
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "ok")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_analysis_symbol_locations_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.symbol_locations_in_source("module app.lib\n\nlet apple = 1\ntype Fruit =\n  | Banana") with
                  | Ok locations -> print(vec_length(locations))
                  | Err _ -> print(0)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=self._native_analysis_service_env())
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "3")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_diagnostics_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  print(vec_length(compiler.diagnostics_in_source("module app.repl\n\nlet broken = missing")))
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=self._native_analysis_service_env())
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "1")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_eval_expr_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn first_or(lines: Vec String, fallback: String) -> String =
                  match lines with
                  | Vec raw ->
                      match vec_get(0, Vec(raw)) with
                      | Just first -> first
                      | Nothing -> fallback

                fn main() -> Unit !{IO} =
                  match compiler.eval_lines_in_source("module app.repl\n\nlet local = 41", "local + 1") with
                  | Ok lines -> print(first_or(lines, "<empty>"))
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "42")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_eval_expr_in_source_builtin_surfaces_service_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.eval_lines_in_source("module app.repl", "print(1)") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertIn("Top-level let bindings must not perform effects", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_instances_in_source_builtin_runs_via_analysis_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler

                fn render_pair(pair: compiler.InstanceMatches) -> String =
                  if vec_length(compiler.instance_match_names(pair)) == 0 then
                    compiler.instance_query_type(pair)
                  else
                    vec_get_or(0, compiler.instance_query_type(pair), compiler.instance_match_names(pair))

                fn main() -> Unit !{IO} =
                  match compiler.instances_in_source("module app.repl", "List Int") with
                  | Ok pair -> print(render_pair(pair))
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertEqual(run.stdout.strip(), "Foldable List")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_instances_in_source_builtin_surfaces_service_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.compiler as compiler
                fn main() -> Unit !{IO} =
                  match compiler.instances_in_source("module app.repl", "(") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=self._native_analysis_service_env(),
            )
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stderr, "")
            self.assertIn("Expected type", run.stdout)


if __name__ == "__main__":
    unittest.main()
