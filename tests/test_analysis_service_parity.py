from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
BIN = ROOT / "analysis_service_bin"
STDLIB = ROOT / "stdlib"

_SIMPLE_SOURCE = "module test\n\nfn foo() -> Int = 42\nfn bar(x: Int) -> Int = x + 1\n"

_TYPE_SOURCE = (
    "module test\n\ntype Color = Red | Green | Blue\n\n"
    "fn describe(c: Color) -> String =\n"
    "  match c with\n"
    "  | Red -> \"red\"\n"
    "  | Green -> \"green\"\n"
    "  | Blue -> \"blue\"\n"
)

_IMPORT_SOURCE = (
    "module test\nimport stdlib.string as string\n\n"
    "fn greet(name: String) -> String = string.concat(\"Hello, \", name)\n"
)

_EXPORT_SOURCE = "module test\n\nexport fn foo() -> Int = 42\nfn _private() -> Int = 0\n"

_INVALID_SOURCE = "module test\nfn bad = (("


def _skip_if_no_bin(cls: type) -> type:
    return unittest.skipUnless(BIN.exists(), f"analysis_service_bin not found at {BIN}")(cls)


def _query(requests: list[dict]) -> list[dict]:
    proc = subprocess.Popen(
        [str(BIN), str(STDLIB)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    stdin_text = "".join(json.dumps(r) + "\n" for r in requests)
    stdout, _ = proc.communicate(input=stdin_text, timeout=30)
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def _req(op: str, source: str) -> dict:
    return {"op": op, "module_source": source}


@_skip_if_no_bin
class AnalysisServiceProtocolTests(unittest.TestCase):
    """Tests for protocol-level correctness: error shapes, unknown ops, invalid input."""

    def test_unknown_op_returns_structured_error(self) -> None:
        [resp] = _query([{"op": "not_a_real_op", "module_source": ""}])
        self.assertFalse(resp["ok"])
        self.assertIn("not_a_real_op", resp["error"])

    def test_invalid_json_returns_error_response(self) -> None:
        proc = subprocess.Popen(
            [str(BIN), str(STDLIB)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        stdout, _ = proc.communicate(input="{not json}\n", timeout=10)
        resp = json.loads(stdout.strip())
        self.assertFalse(resp["ok"])
        self.assertIn("invalid JSON", resp["error"])

    def test_missing_op_field_returns_error(self) -> None:
        [resp] = _query([{"module_source": "module test"}])
        self.assertFalse(resp["ok"])
        self.assertIn("op", resp["error"])

    def test_missing_module_source_field_returns_error(self) -> None:
        [resp] = _query([{"op": "declared_names_in_source"}])
        self.assertFalse(resp["ok"])
        self.assertIn("module_source", resp["error"])

    def test_multiple_requests_handled_in_one_session(self) -> None:
        requests = [
            _req("declared_names_in_source", _SIMPLE_SOURCE),
            _req("declared_names_in_source", _TYPE_SOURCE),
            {"op": "unknown_op", "module_source": ""},
        ]
        responses = _query(requests)
        self.assertEqual(len(responses), 3)
        self.assertTrue(responses[0]["ok"])
        self.assertTrue(responses[1]["ok"])
        self.assertFalse(responses[2]["ok"])

    def test_not_implemented_ops_return_structured_error(self) -> None:
        for op in ("eval_expr_in_source", "type_of_in_source", "instances_in_source", "complete_in_state"):
            with self.subTest(op=op):
                [resp] = _query([{"op": op, "module_source": _SIMPLE_SOURCE}])
                self.assertFalse(resp["ok"])
                self.assertIn("not yet implemented", resp["error"])


@_skip_if_no_bin
class AnalysisServiceDeclaredNamesTests(unittest.TestCase):
    """Tests for declared_names_in_source op."""

    def test_returns_fn_names(self) -> None:
        [resp] = _query([_req("declared_names_in_source", _SIMPLE_SOURCE)])
        self.assertTrue(resp["ok"])
        self.assertIn("foo", resp["value"])
        self.assertIn("bar", resp["value"])

    def test_returns_type_and_constructor_names(self) -> None:
        [resp] = _query([_req("declared_names_in_source", _TYPE_SOURCE)])
        self.assertTrue(resp["ok"])
        self.assertIn("Color", resp["value"])
        self.assertIn("Red", resp["value"])
        self.assertIn("Green", resp["value"])
        self.assertIn("Blue", resp["value"])
        self.assertIn("describe", resp["value"])

    def test_empty_module_returns_empty_list(self) -> None:
        [resp] = _query([_req("declared_names_in_source", "module test\n")])
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["value"], [])


@_skip_if_no_bin
class AnalysisServiceExportedNamesTests(unittest.TestCase):
    """Tests for exported_names_in_source op."""

    def test_export_fn_keyword_includes_name(self) -> None:
        [resp] = _query([_req("exported_names_in_source", _EXPORT_SOURCE)])
        self.assertTrue(resp["ok"])
        self.assertIn("foo", resp["value"])

    def test_non_exported_fn_not_in_exported_names(self) -> None:
        [resp] = _query([_req("exported_names_in_source", _EXPORT_SOURCE)])
        self.assertTrue(resp["ok"])
        self.assertNotIn("_private", resp["value"])

    def test_no_export_keyword_yields_empty_list(self) -> None:
        # The binary uses bundler.scan_source_info which only recognises `export fn` syntax;
        # plain `fn` without the export keyword is NOT included in the exported list.
        [resp] = _query([_req("exported_names_in_source", _SIMPLE_SOURCE)])
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["value"], [])


@_skip_if_no_bin
class AnalysisServiceSymbolInventoryTests(unittest.TestCase):
    """Tests for symbol_inventory_in_source op."""

    def test_declared_includes_fn_names(self) -> None:
        [resp] = _query([_req("symbol_inventory_in_source", _SIMPLE_SOURCE)])
        self.assertTrue(resp["ok"])
        self.assertIn("foo", resp["value"]["declared"])
        self.assertIn("bar", resp["value"]["declared"])

    def test_imported_includes_namespace_alias(self) -> None:
        [resp] = _query([_req("symbol_inventory_in_source", _IMPORT_SOURCE)])
        self.assertTrue(resp["ok"])
        self.assertIn("string", resp["value"]["imported"])

    def test_exported_subset_of_declared(self) -> None:
        [resp] = _query([_req("symbol_inventory_in_source", _EXPORT_SOURCE)])
        self.assertTrue(resp["ok"])
        inv = resp["value"]
        for name in inv["exported"]:
            self.assertIn(name, inv["declared"])


@_skip_if_no_bin
class AnalysisServiceSymbolLocationsTests(unittest.TestCase):
    """Tests for symbol_locations_in_source op."""

    def test_returns_fn_category_for_functions(self) -> None:
        [resp] = _query([_req("symbol_locations_in_source", _SIMPLE_SOURCE)])
        self.assertTrue(resp["ok"])
        loc = resp["value"]
        cat_map = dict(zip(loc["names"], loc["categories"]))
        self.assertEqual(cat_map["foo"], "fn")
        self.assertEqual(cat_map["bar"], "fn")

    def test_returns_type_category_for_type_decl(self) -> None:
        [resp] = _query([_req("symbol_locations_in_source", _TYPE_SOURCE)])
        self.assertTrue(resp["ok"])
        loc = resp["value"]
        cat_map = dict(zip(loc["names"], loc["categories"]))
        self.assertEqual(cat_map.get("Color"), "type")
        self.assertEqual(cat_map.get("describe"), "fn")

    def test_line_numbers_are_positive(self) -> None:
        [resp] = _query([_req("symbol_locations_in_source", _SIMPLE_SOURCE)])
        self.assertTrue(resp["ok"])
        for line in resp["value"]["lines"]:
            self.assertGreater(line, 0)

    def test_parallel_arrays_have_equal_lengths(self) -> None:
        [resp] = _query([_req("symbol_locations_in_source", _TYPE_SOURCE)])
        self.assertTrue(resp["ok"])
        loc = resp["value"]
        n = len(loc["names"])
        self.assertEqual(len(loc["categories"]), n)
        self.assertEqual(len(loc["lines"]), n)
        self.assertEqual(len(loc["columns"]), n)


@_skip_if_no_bin
class AnalysisServiceCheckSourceTests(unittest.TestCase):
    """Tests for check_source op (requires stdlib root)."""

    def test_valid_source_returns_ok(self) -> None:
        [resp] = _query([_req("check_source", _SIMPLE_SOURCE)])
        self.assertTrue(resp["ok"], resp)

    def test_parse_error_returns_failure(self) -> None:
        [resp] = _query([_req("check_source", _INVALID_SOURCE)])
        self.assertFalse(resp["ok"], resp)
