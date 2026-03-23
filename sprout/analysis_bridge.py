from __future__ import annotations

import shlex
import sys

from .analysis_contract import (
    OP_CHECK_SOURCE,
    OP_COMPLETE_IN_STATE,
    OP_DECLARED_NAMES_IN_SOURCE,
    OP_DIAGNOSTICS_IN_SOURCE,
    OP_EXPORTED_NAMES_IN_SOURCE,
    OP_INSTANCES_IN_SOURCE,
    OP_SYMBOL_INVENTORY_IN_SOURCE,
    OP_SYMBOL_LOCATIONS_IN_SOURCE,
    OP_TYPE_OF_IN_SOURCE,
)

ANALYSIS_SERVICE_ENV_VAR = "SPROUT_ANALYSIS_SERVICE_CMD"
ANALYSIS_SERVICE_REQUEST_FAILED = "analysis service: request failed"
ANALYSIS_SERVICE_EMPTY_RESPONSE = "analysis service: empty response"
ANALYSIS_SERVICE_INVALID_RESPONSE = "analysis service: invalid response"
ANALYSIS_SERVICE_START_FAILED = (
    f"analysis service: command failed to start; check {ANALYSIS_SERVICE_ENV_VAR}"
)
REPLAY_SAFE_ANALYSIS_OPS = frozenset(
    {
        OP_CHECK_SOURCE,
        OP_TYPE_OF_IN_SOURCE,
        OP_DECLARED_NAMES_IN_SOURCE,
        OP_EXPORTED_NAMES_IN_SOURCE,
        OP_SYMBOL_INVENTORY_IN_SOURCE,
        OP_DIAGNOSTICS_IN_SOURCE,
        OP_SYMBOL_LOCATIONS_IN_SOURCE,
        OP_INSTANCES_IN_SOURCE,
        OP_COMPLETE_IN_STATE,
    }
)

__all__ = [
    "ANALYSIS_SERVICE_EMPTY_RESPONSE",
    "ANALYSIS_SERVICE_ENV_VAR",
    "ANALYSIS_SERVICE_INVALID_RESPONSE",
    "ANALYSIS_SERVICE_REQUEST_FAILED",
    "ANALYSIS_SERVICE_START_FAILED",
    "REPLAY_SAFE_ANALYSIS_OPS",
    "analysis_service_empty_response_error",
    "analysis_service_env_var_name",
    "analysis_service_invalid_response_error",
    "analysis_service_request_failed_error",
    "analysis_service_retry_allowed",
    "analysis_service_start_error",
    "default_analysis_service_cmd",
]


def default_analysis_service_cmd(python_executable: str | None = None) -> str:
    executable = sys.executable if python_executable is None else python_executable
    return f"{shlex.quote(executable)} -m sprout.analysis_adapter"


def analysis_service_env_var_name() -> str:
    return ANALYSIS_SERVICE_ENV_VAR


def analysis_service_request_failed_error() -> str:
    return ANALYSIS_SERVICE_REQUEST_FAILED


def analysis_service_empty_response_error() -> str:
    return ANALYSIS_SERVICE_EMPTY_RESPONSE


def analysis_service_invalid_response_error() -> str:
    return ANALYSIS_SERVICE_INVALID_RESPONSE


def analysis_service_start_error() -> str:
    return ANALYSIS_SERVICE_START_FAILED


def analysis_service_retry_allowed(op: str) -> bool:
    return op in REPLAY_SAFE_ANALYSIS_OPS
