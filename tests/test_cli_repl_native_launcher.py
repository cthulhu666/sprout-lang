from __future__ import annotations

import unittest

from tests.cli_repl_suite import CliReplTests


_LAUNCHER_TESTS = {
    "test_native_repl_hosted_frontend_runs_end_to_end_non_interactively",
    "test_native_repl_hosted_frontend_evaluates_bare_typeclass_method_values",
    "test_repl_native_launcher_supports_declarations_expressions_and_type_queries",
    "test_repl_native_launcher_block_mode_supports_multiline_function_declaration",
    "test_repl_native_launcher_block_mode_runs_mixed_submissions_sequentially",
    "test_repl_native_launcher_block_mode_supports_multiline_class_declaration",
    "test_repl_native_launcher_block_mode_cancel_discards_buffered_declaration",
    "test_repl_native_launcher_reuses_cached_binary",
}


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name in sorted(_LAUNCHER_TESTS):
        suite.addTest(CliReplTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
