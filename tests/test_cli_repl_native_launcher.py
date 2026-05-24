from __future__ import annotations

import unittest

from tests.cli_repl_suite import CliReplTests


_LAUNCHER_TESTS = {
    "test_repl_native_launcher_supports_declarations_expressions_and_type_queries",
    "test_repl_native_launcher_reuses_cached_binary",
    "test_stdlib_repl_frontend_avoids_legacy_host_hooks",
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
