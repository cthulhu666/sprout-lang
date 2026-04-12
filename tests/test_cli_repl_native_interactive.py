from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.cli_repl_suite import CliReplTests
from sprout.analysis_service_python import default_analysis_service_cmd


_NATIVE_REPL_CACHE_DIR: str | None = None


def setUpModule() -> None:
    global _NATIVE_REPL_CACHE_DIR
    _NATIVE_REPL_CACHE_DIR = tempfile.mkdtemp(prefix="sprout-native-repl-interactive-")
    os.environ["SPROUT_NATIVE_REPL_CACHE_DIR"] = _NATIVE_REPL_CACHE_DIR

    env = dict(os.environ)
    env["SPROUT_ANALYSIS_SERVICE_CMD"] = default_analysis_service_cmd()
    warmup = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "repl", "--native"],
        check=False,
        capture_output=True,
        text=True,
        input=":quit\n",
        env=env,
    )
    if warmup.returncode != 0:
        raise RuntimeError(
            f"failed to warm native REPL cache: {warmup.stderr or warmup.stdout}"
        )


def tearDownModule() -> None:
    if _NATIVE_REPL_CACHE_DIR is None:
        return
    shutil.rmtree(_NATIVE_REPL_CACHE_DIR, ignore_errors=True)
    os.environ.pop("SPROUT_NATIVE_REPL_CACHE_DIR", None)


_INTERACTIVE_TESTS = {
    "test_repl_native_interactive_tab_completion_is_case_insensitive_for_imported_namespaces",
    "test_repl_native_interactive_block_mode_uses_distinct_prompt",
    "test_repl_native_interactive_up_arrow_recalls_history",
    "test_repl_native_launcher_works_without_analysis_service_env_override",
    "test_repl_native_launcher_reports_cache_build_failure_clearly",
    "test_repl_native_launcher_starts_without_analysis_service_for_quit_only",
    "test_repl_native_launcher_reports_bad_analysis_service_on_first_query",
}


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name in sorted(_INTERACTIVE_TESTS):
        suite.addTest(CliReplTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
