from __future__ import annotations

import json
import subprocess
import sys
import unittest
from io import StringIO

from sprout import analysis as sprout_analysis
from sprout.analysis import (
    analysis_complete_in_state,
    analysis_eval_expr_in_source,
    completion_candidates_in_state,
    infer_type_in_source,
    symbol_inventory_in_source,
)
from sprout.analysis_adapter import cmd_analysis_adapter, run_analysis_adapter_session, run_analysis_stdio_session
from sprout.analysis_backend import AnalysisBackend
from sprout.analysis_backend_python import (
    compose_analysis_backend,
    default_analysis_backend,
    default_completion_backend,
    default_execution_backend,
    default_snapshot_backend,
    python_backend_check_source,
    python_backend_eval_expr_in_source,
    python_backend_instances_in_source,
    python_backend_type_of_in_source,
)
from sprout.analysis_backend_stub import StubAnalysisBackend
from sprout.analysis_bridge_runtime import render_analysis_bridge_helpers_c, render_analysis_bridge_request_helpers_c, render_analysis_bridge_response_helpers_c, render_analysis_bridge_runtime_c
from sprout.analysis_cli import cmd_analysis_cli
from sprout.analysis_completion_backend import python_completion_complete_in_state
from sprout.analysis_contract import KEY_MATCHES, KEY_PREFIX, OP_CHECK_SOURCE, OP_COMPLETE_IN_STATE, OP_EVAL_EXPR_IN_SOURCE, response_error, response_ok, request_check_source, request_complete_in_state, request_eval_expr_in_source, request_instances_in_source, request_type_of_in_source
from sprout.analysis_dispatch import dispatch_request
from sprout.analysis_protocol import run_json_service_session
from sprout.analysis_service import cmd_analysis_service
from sprout.analysis_service_config import (
    analysis_service_env_var_name,
    analysis_service_retry_allowed,
    analysis_service_start_error,
    render_analysis_service_retry_allowed_c,
)
from sprout.analysis_service_python import default_analysis_service_cmd
from sprout.analysis_snapshot_backend import (
    symbol_locations_in_source as snapshot_symbol_locations_in_source,
    python_snapshot_declared_names_in_source,
    python_snapshot_diagnostics_in_source,
    python_snapshot_exported_names_in_source,
    python_snapshot_symbol_inventory_in_source,
    python_snapshot_symbol_locations_in_source,
    python_snapshot_symbol_metadata_in_source,
    structured_diagnostics_in_source as snapshot_structured_diagnostics_in_source,
    symbol_metadata_in_source as snapshot_symbol_metadata_in_source,
)
from sprout.analysis_stdio import cmd_analysis_stdio


class CliAnalysisTests(unittest.TestCase):
    def test_analysis_completion_candidates_in_state_matches_imports_and_declarations(self) -> None:
        backend = default_completion_backend()
        prefix, matches = backend.complete_in_state(
            "fr",
            ["import stdlib.bytes (from_string)"],
            ["let answer = 41"],
        )

        self.assertEqual(prefix, "fr")
        self.assertEqual(matches, ["from_string"])
        self.assertEqual(
            (prefix, matches),
            completion_candidates_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )
        self.assertEqual(
            (prefix, matches),
            analysis_complete_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )

    def test_analysis_alias_helpers_match_existing_execution_helpers(self) -> None:
        self.assertEqual(
            analysis_eval_expr_in_source("module app.repl\n\nlet local = 41", "local + 1"),
            ("42",),
        )
        self.assertEqual(
            analysis_complete_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
            ("fr", ["from_string"]),
        )

    def test_analysis_service_config_centralizes_service_env_and_start_error(self) -> None:
        self.assertEqual(analysis_service_env_var_name(), "SPROUT_ANALYSIS_SERVICE_CMD")
        self.assertIn(analysis_service_env_var_name(), analysis_service_start_error())

    def test_analysis_service_python_default_command_targets_neutral_entrypoint(self) -> None:
        cmd = default_analysis_service_cmd()
        self.assertIn("-m sprout.analysis_service_entrypoint", cmd)
        self.assertNotIn("-m sprout.analysis_adapter", cmd)

    def test_analysis_service_config_retry_policy_tracks_replay_safe_operations(self) -> None:
        rendered_policy = render_analysis_service_retry_allowed_c()
        self.assertTrue(analysis_service_retry_allowed(OP_CHECK_SOURCE))
        self.assertTrue(analysis_service_retry_allowed(OP_COMPLETE_IN_STATE))
        self.assertFalse(analysis_service_retry_allowed(OP_EVAL_EXPR_IN_SOURCE))
        self.assertIn(f'strcmp(op, "{OP_CHECK_SOURCE}") == 0', rendered_policy)
        self.assertNotIn(OP_EVAL_EXPR_IN_SOURCE, rendered_policy)

    def test_analysis_bridge_runtime_renders_without_policy_placeholders(self) -> None:
        runtime = render_analysis_bridge_runtime_c(default_analysis_service_cmd())
        self.assertIn("sprout_run_analysis_service", runtime)
        self.assertIn(analysis_service_env_var_name(), runtime)
        self.assertNotIn("__SPROUT_ANALYSIS_SERVICE_", runtime)
        self.assertNotIn("__SPROUT_DEFAULT_ANALYSIS_SERVICE_CMD__", runtime)

    def test_analysis_bridge_helpers_renderer_resolves_service_placeholders(self) -> None:
        helpers = render_analysis_bridge_helpers_c()
        self.assertIn("sprout_analysis_request_source_only", helpers)
        self.assertIn("sprout_analysis_error_from_response", helpers)
        self.assertNotIn("__SPROUT_ANALYSIS_SERVICE_", helpers)

    def test_analysis_bridge_request_helpers_render_shared_source_builders(self) -> None:
        helpers = render_analysis_bridge_request_helpers_c()
        self.assertIn("sprout_analysis_request_source_only", helpers)
        self.assertIn("sprout_analysis_request_source_field", helpers)
        self.assertIn('"analysis service: out of memory"', helpers)

    def test_analysis_bridge_response_helpers_render_shared_result_builders(self) -> None:
        helpers = render_analysis_bridge_response_helpers_c()
        self.assertIn("sprout_analysis_error_from_response", helpers)
        self.assertIn("sprout_analysis_ok_string_result_from_response", helpers)
        self.assertIn("sprout_analysis_ok_vec_string_result_from_response", helpers)
        self.assertIn("sprout_analysis_ok_string_vec_pair_from_response", helpers)
        self.assertIn("sprout_analysis_completion_tuple_or_fail", helpers)
        self.assertIn("sprout_analysis_ok_inventory_from_response", helpers)
        self.assertIn("sprout_analysis_diagnostics_vec_or_fail", helpers)
        self.assertIn("sprout_analysis_ok_symbol_locations_from_response", helpers)

    def test_analysis_service_cli_wrapper_check_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "analysis-service"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_check_source("module app.repl\n\nlet local = 41")),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), response_ok(None))

    def test_analysis_cli_dispatch_rejects_unknown_command(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown analysis cli command"):
            cmd_analysis_cli("not-real")

    def test_analysis_stdio_module_check_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_stdio"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_check_source("module app.repl\n\nlet local = 41")),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), response_ok(None))

    def test_analysis_service_module_wrapper_check_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_service"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_check_source("module app.repl\n\nlet local = 41")),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), response_ok(None))

    def test_analysis_service_entrypoint_module_check_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_service_entrypoint"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_check_source("module app.repl\n\nlet local = 41")),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), response_ok(None))

    def test_analysis_adapter_module_check_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_adapter"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_check_source("module app.repl\n\nlet local = 41")),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), response_ok(None))

    def test_analysis_adapter_session_accepts_injected_backend(self) -> None:
        class FakeBackend(AnalysisBackend):
            def check_source(self, module_source: str) -> None:
                raise AssertionError("unexpected check_source call")

            def type_of_in_source(self, module_source: str, expr: str) -> str:
                self.seen = (module_source, expr)
                return "Fake"

            def declared_names_in_source(self, module_source: str) -> list[str]:
                raise AssertionError("unexpected declared_names_in_source call")

            def exported_names_in_source(self, module_source: str) -> list[str]:
                raise AssertionError("unexpected exported_names_in_source call")

            def symbol_inventory_in_source(self, module_source: str) -> tuple[list[str], list[str], list[str]]:
                raise AssertionError("unexpected symbol_inventory_in_source call")

            def diagnostics_in_source(self, module_source: str) -> list[tuple[str, int, int]]:
                raise AssertionError("unexpected diagnostics_in_source call")

            def symbol_locations_in_source(self, module_source: str) -> list[tuple[str, str, int, int]]:
                raise AssertionError("unexpected symbol_locations_in_source call")

            def instances_in_source(self, module_source: str, query: str) -> tuple[str, list[str]]:
                raise AssertionError("unexpected instances_in_source call")

            def eval_expr_in_source(self, module_source: str, expr: str) -> tuple[str, ...]:
                raise AssertionError("unexpected eval_expr_in_source call")

            def complete_in_state(self, line_buffer: str, imports: list[str], declarations: list[str]) -> tuple[str, list[str]]:
                raise AssertionError("unexpected complete_in_state call")

        fake_backend = FakeBackend()
        stdin = StringIO(json.dumps(request_type_of_in_source("module app.repl\n\nlet local = 41", "local + 1")))
        stdout = StringIO()

        exit_code = run_analysis_adapter_session(stdin=stdin, stdout=stdout, backend=fake_backend)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), response_ok("Fake"))
        self.assertEqual(fake_backend.seen, ("module app.repl\n\nlet local = 41", "local + 1"))

    def test_analysis_adapter_session_accepts_explicit_backend_bundles(self) -> None:
        class FakeExecutionBackend:
            def check_source(self, module_source: str) -> None:
                raise AssertionError("unexpected check_source call")

            def type_of_in_source(self, module_source: str, expr: str) -> str:
                self.seen = (module_source, expr)
                return "BundleFake"

            def instances_in_source(self, module_source: str, query: str) -> tuple[str, list[str]]:
                raise AssertionError("unexpected instances_in_source call")

            def eval_expr_in_source(self, module_source: str, expr: str) -> tuple[str, ...]:
                raise AssertionError("unexpected eval_expr_in_source call")

        fake_backend = FakeExecutionBackend()
        stdin = StringIO(json.dumps(request_type_of_in_source("module app.repl\n\nlet local = 41", "local + 1")))
        stdout = StringIO()

        exit_code = run_analysis_adapter_session(stdin=stdin, stdout=stdout, execution_backend=fake_backend)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), response_ok("BundleFake"))
        self.assertEqual(fake_backend.seen, ("module app.repl\n\nlet local = 41", "local + 1"))

    def test_analysis_adapter_session_supports_stub_backend_inventory_result(self) -> None:
        stub_backend = StubAnalysisBackend(
            symbol_inventory_result=(["local"], ["string"], ["local"]),
        )
        stdin = StringIO(json.dumps({"op": "symbol_inventory_in_source", "module_source": "module app.repl"}))
        stdout = StringIO()

        exit_code = run_analysis_adapter_session(stdin=stdin, stdout=stdout, backend=stub_backend)

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            response_ok({"declared": ["local"], "imported": ["string"], "exported": ["local"]}),
        )
        self.assertEqual(stub_backend.seen_calls, [("symbol_inventory_in_source", "module app.repl")])

    def test_analysis_stdio_type_of_in_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_stdio"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_type_of_in_source("module app.repl\n\nlet local = 41", "local + 1")),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), response_ok("Int"))

    def test_analysis_stdio_instances_in_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_stdio"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_instances_in_source("module app.repl", "List Int")),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(
            json.loads(run.stdout),
            response_ok(
                {
                    "matches": ["Foldable List", "Functor List", "Monoid (List a)", "Semigroup (List a)", "ToString (List a)"],
                    "query_type": "List Int",
                }
            ),
        )

    def test_analysis_stdio_eval_expr_in_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_stdio"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_eval_expr_in_source("module app.repl\n\nlet local = 41", "local + 1")),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), response_ok(["42"]))

    def test_analysis_stdio_complete_in_state_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_stdio"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(request_complete_in_state("fr", ["import stdlib.bytes (from_string)"], ["let answer = 41"])),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(
            json.loads(run.stdout),
            response_ok({KEY_MATCHES: ["from_string"], KEY_PREFIX: "fr"}),
        )

    def test_analysis_stdio_reports_unknown_operation(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.analysis_stdio"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps({"op": "not-real"}),
        )
        self.assertEqual(run.returncode, 1, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(
            json.loads(run.stdout),
            response_error("unknown analysis service op `not-real`"),
        )

    def test_analysis_service_cli_wrapper_reports_unknown_operation(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "analysis-service"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps({"op": "not-real"}),
        )
        self.assertEqual(run.returncode, 1, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(
            json.loads(run.stdout),
            response_error("unknown analysis service op `not-real`"),
        )

    def test_analysis_service_processes_multiple_line_delimited_requests(self) -> None:
        stdin = StringIO(
            "\n".join(
                [
                    json.dumps(request_check_source("module app.repl\n\nlet local = 41")),
                    json.dumps(request_type_of_in_source("module app.repl\n\nlet local = 41", "local")),
                ]
            )
            + "\n"
        )
        stdout = StringIO()

        status = cmd_analysis_service(stdin=stdin, stdout=stdout)

        self.assertEqual(status, 0)
        self.assertEqual(
            [json.loads(line) for line in stdout.getvalue().splitlines()],
            [
                response_ok(None),
                response_ok("Int"),
            ],
        )

    def test_analysis_stdio_processes_multiple_line_delimited_requests(self) -> None:
        stdin = StringIO(
            "\n".join(
                [
                    json.dumps(request_check_source("module app.repl\n\nlet local = 41")),
                    json.dumps(request_type_of_in_source("module app.repl\n\nlet local = 41", "local")),
                ]
            )
            + "\n"
        )
        stdout = StringIO()

        status = cmd_analysis_stdio(stdin=stdin, stdout=stdout)

        self.assertEqual(status, 0)
        self.assertEqual(
            [json.loads(line) for line in stdout.getvalue().splitlines()],
            [
                response_ok(None),
                response_ok("Int"),
            ],
        )

    def test_analysis_adapter_processes_multiple_line_delimited_requests(self) -> None:
        stdin = StringIO(
            "\n".join(
                [
                    json.dumps(request_check_source("module app.repl\n\nlet local = 41")),
                    json.dumps(request_type_of_in_source("module app.repl\n\nlet local = 41", "local")),
                ]
            )
            + "\n"
        )
        stdout = StringIO()

        status = run_analysis_stdio_session(stdin=stdin, stdout=stdout)

        self.assertEqual(status, 0)
        self.assertEqual(
            [json.loads(line) for line in stdout.getvalue().splitlines()],
            [
                response_ok(None),
                response_ok("Int"),
            ],
        )

    def test_analysis_adapter_stdio_alias_matches_neutral_runner(self) -> None:
        stdin = StringIO(json.dumps(request_check_source("module app.repl\n\nlet local = 41")) + "\n")
        stdout = StringIO()

        status = run_analysis_adapter_session(stdin=stdin, stdout=stdout)

        self.assertEqual(status, 0)
        self.assertEqual(
            [json.loads(line) for line in stdout.getvalue().splitlines()],
            [response_ok(None)],
        )

    def test_analysis_adapter_command_uses_neutral_runner(self) -> None:
        stdin = StringIO(json.dumps(request_check_source("module app.repl\n\nlet local = 41")) + "\n")
        stdout = StringIO()

        status = cmd_analysis_adapter(stdin=stdin, stdout=stdout)

        self.assertEqual(status, 0)
        self.assertEqual(
            [json.loads(line) for line in stdout.getvalue().splitlines()],
            [response_ok(None)],
        )

    def test_analysis_protocol_reports_invalid_line_delimited_json(self) -> None:
        stdin = StringIO("{not json}\n")
        stdout = StringIO()

        status = run_json_service_session(
            stdin=stdin,
            stdout=stdout,
            dispatch=dispatch_request,
        )

        self.assertEqual(status, 1)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            response_error("invalid request json: Expecting property name enclosed in double quotes"),
        )

    def test_analysis_dispatch_reports_structured_unknown_operation(self) -> None:
        self.assertEqual(
            dispatch_request({"op": "not-real"}),
            response_error("unknown analysis service op `not-real`"),
        )

    def test_analysis_dispatch_complete_in_state_returns_structured_success(self) -> None:
        self.assertEqual(
            dispatch_request(request_complete_in_state("fr", ["import stdlib.bytes (from_string)"], ["let answer = 41"])),
            response_ok({KEY_MATCHES: ["from_string"], KEY_PREFIX: "fr"}),
        )

    def test_analysis_dispatch_accepts_injected_backend(self) -> None:
        class FakeBackend:
            def check_source(self, module_source: str) -> None:
                raise AssertionError("unexpected")

            def type_of_in_source(self, module_source: str, expr: str) -> str:
                return "Fake"

            def declared_names_in_source(self, module_source: str) -> list[str]:
                raise AssertionError("unexpected")

            def exported_names_in_source(self, module_source: str) -> list[str]:
                raise AssertionError("unexpected")

            def symbol_inventory_in_source(self, module_source: str) -> tuple[list[str], list[str], list[str]]:
                raise AssertionError("unexpected")

            def diagnostics_in_source(self, module_source: str) -> list[tuple[str, int, int]]:
                raise AssertionError("unexpected")

            def symbol_locations_in_source(self, module_source: str) -> list[tuple[str, str, int, int]]:
                raise AssertionError("unexpected")

            def instances_in_source(self, module_source: str, query: str) -> tuple[str, list[str]]:
                raise AssertionError("unexpected")

            def eval_expr_in_source(self, module_source: str, expr: str) -> tuple[str, ...]:
                raise AssertionError("unexpected")

            def complete_in_state(self, line_buffer: str, imports: list[str], declarations: list[str]) -> tuple[str, list[str]]:
                raise AssertionError("unexpected")

        self.assertEqual(
            dispatch_request(request_type_of_in_source("module app.repl\n\nlet local = 41", "local"), backend=FakeBackend()),
            response_ok("Fake"),
        )

    def test_analysis_dispatch_accepts_explicit_backend_bundles(self) -> None:
        class FakeSnapshotBackend:
            def declared_names_in_source(self, module_source: str) -> list[str]:
                raise AssertionError("unexpected")

            def exported_names_in_source(self, module_source: str) -> list[str]:
                raise AssertionError("unexpected")

            def symbol_inventory_in_source(self, module_source: str) -> tuple[list[str], list[str], list[str]]:
                return (["declared"], ["imported"], ["exported"])

            def diagnostics_in_source(self, module_source: str) -> list[tuple[str, int, int]]:
                raise AssertionError("unexpected")

            def symbol_locations_in_source(self, module_source: str) -> list[tuple[str, str, int, int]]:
                raise AssertionError("unexpected")

        self.assertEqual(
            dispatch_request(
                {"op": "symbol_inventory_in_source", "module_source": "module app.repl"},
                snapshot_backend=FakeSnapshotBackend(),
            ),
            response_ok({"declared": ["declared"], "imported": ["imported"], "exported": ["exported"]}),
        )

    def test_analysis_backend_type_query_matches_analysis_surface(self) -> None:
        self.assertEqual(
            python_backend_type_of_in_source("module app.repl\n\nlet local = 41", "local"),
            "Int",
        )

    def test_analysis_backend_default_type_query_matches_analysis_surface(self) -> None:
        self.assertEqual(
            default_analysis_backend().type_of_in_source("module app.repl\n\nlet local = 41", "local"),
            infer_type_in_source("module app.repl\n\nlet local = 41", "local"),
        )

    def test_compose_analysis_backend_combines_backend_bundles(self) -> None:
        snapshot_backend = default_snapshot_backend()
        execution_backend = default_execution_backend()
        completion_backend = default_completion_backend()
        backend = compose_analysis_backend(
            snapshot_backend=snapshot_backend,
            execution_backend=execution_backend,
            completion_backend=completion_backend,
        )

        source = "module app.repl\n\nlet local = 41"
        self.assertEqual(backend.type_of_in_source(source, "local"), execution_backend.type_of_in_source(source, "local"))
        self.assertEqual(backend.symbol_inventory_in_source(source), snapshot_backend.symbol_inventory_in_source(source))
        self.assertEqual(
            backend.complete_in_state("lo", [], ["let local = 41"]),
            completion_backend.complete_in_state("lo", [], ["let local = 41"]),
        )

    def test_python_execution_backend_helpers_match_default_execution_backend(self) -> None:
        source = "module app.repl\n\nlet local = 41"
        backend = default_execution_backend()

        self.assertIsNone(python_backend_check_source(source))
        self.assertIsNone(backend.check_source(source))
        self.assertEqual(python_backend_type_of_in_source(source, "local"), backend.type_of_in_source(source, "local"))
        self.assertEqual(
            python_backend_instances_in_source(source, "List Int"),
            backend.instances_in_source(source, "List Int"),
        )
        self.assertEqual(
            python_backend_eval_expr_in_source(source, "local + 1"),
            backend.eval_expr_in_source(source, "local + 1"),
        )

    def test_default_snapshot_backend_matches_canonical_analysis_surface(self) -> None:
        source = "\n".join(
            [
                "module app.repl",
                "",
                "import stdlib.string as string",
                "export let local = string.concat(\"a\", \"b\")",
            ]
        )

        def normalize_metadata(entries: list[object]) -> list[tuple[object, ...]]:
            normalized: list[tuple[object, ...]] = []
            for entry in entries:
                location = getattr(entry, "location")
                definition_location = getattr(entry, "definition_location")
                normalized.append(
                    (
                        getattr(entry, "visible_name"),
                        getattr(entry, "kind"),
                        getattr(entry, "canonical_name"),
                        getattr(entry, "origin_module"),
                        getattr(location, "line"),
                        getattr(location, "column"),
                        None
                        if definition_location is None
                        else (getattr(definition_location, "line"), getattr(definition_location, "column")),
                        getattr(entry, "introduced_via"),
                        getattr(entry, "exported"),
                        getattr(entry, "imported_from_module"),
                    )
                )
            return normalized

        def normalize_diagnostics(entries: list[object]) -> list[tuple[object, ...]]:
            normalized: list[tuple[object, ...]] = []
            for entry in entries:
                location = getattr(entry, "location")
                normalized.append(
                    (
                        getattr(entry, "severity"),
                        getattr(entry, "stage"),
                        getattr(entry, "message"),
                        None if location is None else (getattr(location, "line"), getattr(location, "column")),
                    )
                )
            return normalized

        backend = default_snapshot_backend()
        self.assertEqual(backend.declared_names_in_source(source), python_snapshot_declared_names_in_source(source))
        self.assertEqual(backend.declared_names_in_source(source), sprout_analysis.declared_names_in_source(source))
        self.assertEqual(backend.exported_names_in_source(source), python_snapshot_exported_names_in_source(source))
        self.assertEqual(backend.exported_names_in_source(source), sprout_analysis.exported_names_in_source(source))
        self.assertEqual(backend.symbol_inventory_in_source(source), python_snapshot_symbol_inventory_in_source(source))
        self.assertEqual(backend.symbol_inventory_in_source(source), sprout_analysis.symbol_inventory_in_source(source))
        self.assertEqual(backend.diagnostics_in_source(source), python_snapshot_diagnostics_in_source(source))
        self.assertEqual(backend.diagnostics_in_source(source), sprout_analysis.diagnostics_in_source(source))
        self.assertEqual(backend.symbol_locations_in_source(source), python_snapshot_symbol_locations_in_source(source))
        self.assertEqual(backend.symbol_locations_in_source(source), sprout_analysis.symbol_locations_in_source(source))
        self.assertEqual(
            normalize_metadata(python_snapshot_symbol_metadata_in_source(source)),
            normalize_metadata(snapshot_symbol_metadata_in_source(source)),
        )
        self.assertEqual(
            normalize_metadata(snapshot_symbol_metadata_in_source(source)),
            normalize_metadata(sprout_analysis.symbol_metadata_in_source(source)),
        )
        self.assertEqual(
            normalize_diagnostics(snapshot_structured_diagnostics_in_source(source)),
            normalize_diagnostics(sprout_analysis.structured_diagnostics_in_source(source)),
        )

    def test_default_execution_backend_matches_canonical_analysis_surface(self) -> None:
        source = "module app.repl\n\nlet local = 41"
        backend = default_execution_backend()

        self.assertIsNone(backend.check_source(source))
        self.assertEqual(backend.type_of_in_source(source, "local"), infer_type_in_source(source, "local"))
        self.assertEqual(backend.instances_in_source(source, "List Int"), sprout_analysis.instances_in_source(source, "List Int"))
        self.assertEqual(backend.eval_expr_in_source(source, "local + 1"), analysis_eval_expr_in_source(source, "local + 1"))

    def test_default_completion_backend_matches_canonical_analysis_surface(self) -> None:
        backend = default_completion_backend()
        expected = backend.complete_in_state(
            "fr",
            ["import stdlib.bytes (from_string)"],
            ["let answer = 41"],
        )

        self.assertEqual(expected, python_completion_complete_in_state(
            "fr",
            ["import stdlib.bytes (from_string)"],
            ["let answer = 41"],
        ))
        self.assertEqual(
            expected,
            completion_candidates_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )
        self.assertEqual(
            expected,
            analysis_complete_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )

    def test_analysis_backend_symbol_inventory_matches_analysis_surface(self) -> None:
        source = "\n".join(
            [
                "module app.repl",
                "",
                "import stdlib.string as string",
                "export let local = string.concat(\"a\", \"b\")",
            ]
        )

        self.assertEqual(
            default_analysis_backend().symbol_inventory_in_source(source),
            symbol_inventory_in_source(source),
        )

    def test_analysis_snapshot_backend_symbol_inventory_matches_analysis_surface(self) -> None:
        source = "\n".join(
            [
                "module app.repl",
                "",
                "import stdlib.string as string",
                "export let local = string.concat(\"a\", \"b\")",
            ]
        )

        self.assertEqual(
            python_snapshot_symbol_inventory_in_source(source),
            symbol_inventory_in_source(source),
        )

    def test_analysis_snapshot_backend_symbol_locations_match_analysis_surface(self) -> None:
        source = "\n".join(
            [
                "module app.lib",
                "",
                "let alpha = 1",
                "",
                "fn beta(x: Int) -> Int = x",
                "",
                "class Render a {",
                "  fn render(value: a) -> String",
                "}",
                "",
                "type Box =",
                "  | Wrap String",
            ]
        )

        self.assertEqual(
            python_snapshot_symbol_locations_in_source(source),
            sprout_analysis.symbol_locations_in_source(source),
        )

    def test_analysis_backend_completion_matches_analysis_surface(self) -> None:
        expected = default_completion_backend().complete_in_state(
            "fr",
            ["import stdlib.bytes (from_string)"],
            ["let answer = 41"],
        )
        self.assertEqual(
            expected,
            default_analysis_backend().complete_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )
        self.assertEqual(
            expected,
            completion_candidates_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )

    def test_analysis_completion_backend_matches_analysis_surface(self) -> None:
        expected = default_completion_backend().complete_in_state(
            "fr",
            ["import stdlib.bytes (from_string)"],
            ["let answer = 41"],
        )
        self.assertEqual(
            expected,
            python_completion_complete_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )
        self.assertEqual(
            expected,
            completion_candidates_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )
        self.assertEqual(
            expected,
            analysis_complete_in_state(
                "fr",
                ["import stdlib.bytes (from_string)"],
                ["let answer = 41"],
            ),
        )

    def test_analysis_symbol_locations_in_source_reports_top_level_locations(self) -> None:
        locations = snapshot_symbol_locations_in_source(
            "module app.lib\n\nlet alpha = 1\n\nfn beta(x: Int) -> Int = x\n\nclass Render a {\n  fn render(value: a) -> String\n}\n\ntype Box =\n  | Wrap String"
        )

        self.assertIn(("value", "alpha", 3, 1), locations)
        self.assertIn(("value", "beta", 5, 1), locations)
        self.assertIn(("class", "Render", 7, 1), locations)
        self.assertIn(("type", "Box", 11, 1), locations)
        self.assertIn(("constructor", "Wrap", 12, 5), locations)

    def test_analysis_symbol_metadata_in_source_reports_declared_and_imported_symbols(self) -> None:
        entries = snapshot_symbol_metadata_in_source(
            "module app.lib\n\nimport stdlib.string\nimport stdlib.string (concat)\nimport stdlib.bytes (from_string)\n\nexport type Box(..) =\n  | Wrap String\n\nexport fn unwrap(value: Box) -> String =\n  match value with\n  | Wrap raw -> raw\n\nlet local = 1"
        )

        by_name = {entry.visible_name: entry for entry in entries}

        self.assertEqual(by_name["Box"].kind, "type")
        self.assertEqual(by_name["Box"].canonical_name, "app.lib.Box")
        self.assertEqual(by_name["Box"].location.line, 7)
        self.assertEqual(by_name["Box"].location.column, 1)
        self.assertTrue(by_name["Box"].exported)

        self.assertEqual(by_name["Wrap"].kind, "constructor")
        self.assertEqual(by_name["Wrap"].canonical_name, "app.lib.Wrap")
        self.assertEqual(by_name["Wrap"].location.line, 8)
        self.assertEqual(by_name["Wrap"].location.column, 5)
        self.assertTrue(by_name["Wrap"].exported)

        self.assertEqual(by_name["unwrap"].kind, "value")
        self.assertEqual(by_name["unwrap"].canonical_name, "app.lib.unwrap")
        self.assertEqual(by_name["unwrap"].location.line, 10)
        self.assertEqual(by_name["unwrap"].location.column, 1)
        self.assertTrue(by_name["unwrap"].exported)

        self.assertEqual(by_name["local"].kind, "value")
        self.assertEqual(by_name["local"].canonical_name, "app.lib.local")
        self.assertEqual(by_name["local"].location.line, 14)
        self.assertEqual(by_name["local"].location.column, 1)
        self.assertFalse(by_name["local"].exported)
        self.assertEqual(by_name["local"].definition_location, by_name["local"].location)

        self.assertEqual(by_name["string"].kind, "module_alias")
        self.assertEqual(by_name["string"].introduced_via, "namespace")
        self.assertEqual(by_name["string"].imported_from_module, "stdlib.string")
        self.assertIsNone(by_name["string"].definition_location)

        self.assertEqual(by_name["concat"].kind, "value")
        self.assertEqual(by_name["concat"].canonical_name, "stdlib.string.concat")
        self.assertEqual(by_name["concat"].introduced_via, "imported")
        self.assertEqual(by_name["concat"].imported_from_module, "stdlib.string")
        self.assertIsNotNone(by_name["concat"].definition_location)
        assert by_name["concat"].definition_location is not None
        self.assertEqual(by_name["concat"].definition_location.path.name, "string.sprout")
        self.assertEqual(by_name["concat"].definition_location.line, 7)
        self.assertEqual(by_name["concat"].definition_location.column, 1)

        self.assertEqual(by_name["from_string"].kind, "value")
        self.assertEqual(by_name["from_string"].introduced_via, "imported")
        self.assertEqual(by_name["from_string"].imported_from_module, "stdlib.bytes")
        self.assertIsNotNone(by_name["from_string"].definition_location)
        assert by_name["from_string"].definition_location is not None
        self.assertEqual(by_name["from_string"].definition_location.path.name, "bytes.sprout")
        self.assertEqual(by_name["from_string"].definition_location.line, 24)
        self.assertEqual(by_name["from_string"].definition_location.column, 1)

    def test_structured_diagnostics_in_source_reports_stage_and_location(self) -> None:
        type_diagnostics = snapshot_structured_diagnostics_in_source(
            "module app.repl\n\nlet broken = missing"
        )
        self.assertEqual(len(type_diagnostics), 1)
        type_diag = type_diagnostics[0]
        self.assertEqual(type_diag.severity, "error")
        self.assertEqual(type_diag.stage, "typecheck")
        self.assertEqual(type_diag.message, "Unknown variable missing")
        self.assertIsNotNone(type_diag.location)
        assert type_diag.location is not None
        self.assertEqual(type_diag.location.line, 3)
        self.assertEqual(type_diag.location.column, 14)

        parse_diagnostics = snapshot_structured_diagnostics_in_source(
            "module app.repl\n\nlet broken ="
        )
        self.assertEqual(len(parse_diagnostics), 1)
        parse_diag = parse_diagnostics[0]
        self.assertEqual(parse_diag.severity, "error")
        self.assertEqual(parse_diag.stage, "parse")
        self.assertIsNotNone(parse_diag.location)

    def test_analysis_backend_default_helper_returns_python_backend(self) -> None:
        self.assertEqual(default_analysis_backend().type_of_in_source("module app.repl\n\nlet local = 41", "local"), "Int")

    def test_analysis_contract_check_source_request_uses_canonical_op_name(self) -> None:
        self.assertEqual(
            request_check_source("module app.repl"),
            {"op": OP_CHECK_SOURCE, "module_source": "module app.repl"},
        )
