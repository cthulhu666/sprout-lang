from __future__ import annotations

import unittest

from tests.cli_repl_suite import CliReplTests


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name in sorted(
        name
        for name in dir(CliReplTests)
        if name.startswith("test_") and "native" not in name
    ):
        suite.addTest(CliReplTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
