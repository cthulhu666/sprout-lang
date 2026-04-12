from __future__ import annotations

import os
import pty
import re
import shlex
import unittest
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import sys

from sprout import CodegenError, compile_to_llvm, parse, typecheck_program
from sprout import cli as sprout_cli
from sprout.analysis_service_config import default_analysis_service_cmd
from sprout.module_loader import load_module_bundle, resolve_program_names
from tests.integration_support import compiled_native_binary, running_https_server, running_tcp_fixture




class CodegenTestCase(unittest.TestCase):
    def _native_analysis_service_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["SPROUT_ANALYSIS_SERVICE_CMD"] = default_analysis_service_cmd()
        return env

    def _load_module_program(self, source: str, *, filename: str = "main.sprout"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / filename
            path.write_text(source, encoding="utf-8")
            bundle = load_module_bundle(path)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            return program
