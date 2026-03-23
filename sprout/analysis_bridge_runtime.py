from __future__ import annotations

import json

from .analysis_bridge import (
    analysis_service_empty_response_error,
    analysis_service_env_var_name,
    analysis_service_invalid_response_error,
    analysis_service_request_failed_error,
    analysis_service_start_error,
)
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

__all__ = [
    "render_analysis_bridge_request_helpers_c",
    "render_analysis_bridge_runtime_c",
]


def render_analysis_bridge_runtime_c(embedded_analysis_service_cmd: str) -> str:
    return (
        """
static FILE* sprout_analysis_service_in = NULL;
static FILE* sprout_analysis_service_out = NULL;
static pid_t sprout_analysis_service_pid = -1;
static int sprout_analysis_service_atexit_registered = 0;
static int sprout_analysis_service_sigpipe_ignored = 0;
static int sprout_analysis_service_last_status = 0;
static int sprout_analysis_service_last_status_valid = 0;
static void sprout_record_analysis_service_status(int status) {
  sprout_analysis_service_last_status = status;
  sprout_analysis_service_last_status_valid = 1;
}
static int sprout_analysis_service_command_not_found(void) {
  return sprout_analysis_service_last_status_valid
    && WIFEXITED(sprout_analysis_service_last_status)
    && WEXITSTATUS(sprout_analysis_service_last_status) == 127;
}
static void sprout_close_analysis_service(void) {
  if (sprout_analysis_service_in != NULL) {
    fclose(sprout_analysis_service_in);
    sprout_analysis_service_in = NULL;
  }
  if (sprout_analysis_service_out != NULL) {
    fclose(sprout_analysis_service_out);
    sprout_analysis_service_out = NULL;
  }
  if (sprout_analysis_service_pid > 0) {
    int status = 0;
    if (waitpid(sprout_analysis_service_pid, &status, 0) == sprout_analysis_service_pid) {
      sprout_record_analysis_service_status(status);
    }
    sprout_analysis_service_pid = -1;
  }
}
static int sprout_analysis_service_is_stale(void) {
  if (sprout_analysis_service_pid <= 0) return 0;
  int status = 0;
  pid_t waited = waitpid(sprout_analysis_service_pid, &status, WNOHANG);
  if (waited == 0) return 0;
  if (waited == sprout_analysis_service_pid) {
    sprout_record_analysis_service_status(status);
    sprout_analysis_service_pid = -1;
    return 1;
  }
  return 0;
}
static int sprout_analysis_service_retry_allowed(const char* op) {
  return strcmp(op, "__SPROUT_ANALYSIS_OP_CHECK_SOURCE__") == 0
    || strcmp(op, "__SPROUT_ANALYSIS_OP_TYPE_OF_IN_SOURCE__") == 0
    || strcmp(op, "__SPROUT_ANALYSIS_OP_DECLARED_NAMES_IN_SOURCE__") == 0
    || strcmp(op, "__SPROUT_ANALYSIS_OP_EXPORTED_NAMES_IN_SOURCE__") == 0
    || strcmp(op, "__SPROUT_ANALYSIS_OP_SYMBOL_INVENTORY_IN_SOURCE__") == 0
    || strcmp(op, "__SPROUT_ANALYSIS_OP_DIAGNOSTICS_IN_SOURCE__") == 0
    || strcmp(op, "__SPROUT_ANALYSIS_OP_SYMBOL_LOCATIONS_IN_SOURCE__") == 0
    || strcmp(op, "__SPROUT_ANALYSIS_OP_INSTANCES_IN_SOURCE__") == 0
    || strcmp(op, "__SPROUT_ANALYSIS_OP_COMPLETE_IN_STATE__") == 0;
}
static int sprout_ensure_analysis_service(char** error_out) {
  if (!sprout_analysis_service_sigpipe_ignored) {
    signal(SIGPIPE, SIG_IGN);
    sprout_analysis_service_sigpipe_ignored = 1;
  }
  if (sprout_analysis_service_is_stale()) {
    sprout_close_analysis_service();
  }
  if (sprout_analysis_service_in != NULL && sprout_analysis_service_out != NULL && sprout_analysis_service_pid > 0) {
    return 1;
  }
  sprout_analysis_service_last_status_valid = 0;
  const char* cmd = getenv("__SPROUT_ANALYSIS_SERVICE_ENV_VAR__");
  if (cmd == NULL || *cmd == '\\0') cmd = "__SPROUT_DEFAULT_ANALYSIS_SERVICE_CMD__";
  int request_pipe[2] = {-1, -1};
  int response_pipe[2] = {-1, -1};
  if (pipe(request_pipe) != 0 || pipe(response_pipe) != 0) {
    if (request_pipe[0] >= 0) close(request_pipe[0]);
    if (request_pipe[1] >= 0) close(request_pipe[1]);
    if (response_pipe[0] >= 0) close(response_pipe[0]);
    if (response_pipe[1] >= 0) close(response_pipe[1]);
    *error_out = dup_cstr("analysis service: unable to create pipes");
    return 0;
  }
  pid_t pid = fork();
  if (pid < 0) {
    close(request_pipe[0]);
    close(request_pipe[1]);
    close(response_pipe[0]);
    close(response_pipe[1]);
    *error_out = dup_cstr("analysis service: unable to fork");
    return 0;
  }
  if (pid == 0) {
    dup2(request_pipe[0], STDIN_FILENO);
    dup2(response_pipe[1], STDOUT_FILENO);
    freopen("/dev/null", "w", stderr);
    close(request_pipe[0]);
    close(request_pipe[1]);
    close(response_pipe[0]);
    close(response_pipe[1]);
    execl("/bin/sh", "sh", "-lc", cmd, (char*)NULL);
    _exit(127);
  }
  close(request_pipe[0]);
  close(response_pipe[1]);
  FILE* in_file = fdopen(request_pipe[1], "w");
  FILE* out_file = fdopen(response_pipe[0], "r");
  if (in_file == NULL || out_file == NULL) {
    if (in_file != NULL) fclose(in_file);
    else close(request_pipe[1]);
    if (out_file != NULL) fclose(out_file);
    else close(response_pipe[0]);
    close(request_pipe[0]);
    close(response_pipe[1]);
    waitpid(pid, NULL, 0);
    *error_out = dup_cstr("analysis service: unable to open pipes");
    return 0;
  }
  setvbuf(in_file, NULL, _IOLBF, 0);
  sprout_analysis_service_in = in_file;
  sprout_analysis_service_out = out_file;
  sprout_analysis_service_pid = pid;
  if (!sprout_analysis_service_atexit_registered) {
    atexit(sprout_close_analysis_service);
    sprout_analysis_service_atexit_registered = 1;
  }
  return 1;
}
static int sprout_run_analysis_service(const char* request_json, int retry_once, char** response_out, char** error_out) {
  int max_attempts = retry_once ? 2 : 1;
  for (int attempt = 0; attempt < max_attempts; attempt++) {
    if (!sprout_ensure_analysis_service(error_out)) return 0;
    if (fputs(request_json, sprout_analysis_service_in) == EOF || fflush(sprout_analysis_service_in) != 0) {
      sprout_close_analysis_service();
      if (attempt + 1 < max_attempts) continue;
      *error_out = dup_cstr("__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
      return 0;
    }
    char* response = NULL;
    size_t response_cap = 0;
    ssize_t response_len = getline(&response, &response_cap, sprout_analysis_service_out);
    if (response_len < 0) {
      if (response != NULL) free(response);
      sprout_close_analysis_service();
      if (attempt + 1 < max_attempts) continue;
      if (sprout_analysis_service_command_not_found()) *error_out = dup_cstr("__SPROUT_ANALYSIS_SERVICE_START_FAILED__");
      else *error_out = dup_cstr("__SPROUT_ANALYSIS_SERVICE_EMPTY_RESPONSE__");
      return 0;
    }
    if (response_len > 0 && response[response_len - 1] == '\\n') {
      response[response_len - 1] = '\\0';
    }
    *response_out = response;
    return 1;
  }
  *error_out = dup_cstr("__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
  return 0;
}
""".replace(
            '"__SPROUT_DEFAULT_ANALYSIS_SERVICE_CMD__"',
            json.dumps(embedded_analysis_service_cmd),
        ).replace(
            '"__SPROUT_ANALYSIS_SERVICE_ENV_VAR__"',
            json.dumps(analysis_service_env_var_name()),
        ).replace(
            '"__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__"',
            json.dumps(analysis_service_request_failed_error()),
        ).replace(
            '"__SPROUT_ANALYSIS_SERVICE_EMPTY_RESPONSE__"',
            json.dumps(analysis_service_empty_response_error()),
        ).replace(
            '"__SPROUT_ANALYSIS_SERVICE_START_FAILED__"',
            json.dumps(analysis_service_start_error()),
        ).replace(
            "__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__",
            analysis_service_invalid_response_error(),
        ).replace(
            "__SPROUT_ANALYSIS_OP_CHECK_SOURCE__",
            OP_CHECK_SOURCE,
        ).replace(
            "__SPROUT_ANALYSIS_OP_TYPE_OF_IN_SOURCE__",
            OP_TYPE_OF_IN_SOURCE,
        ).replace(
            "__SPROUT_ANALYSIS_OP_DECLARED_NAMES_IN_SOURCE__",
            OP_DECLARED_NAMES_IN_SOURCE,
        ).replace(
            "__SPROUT_ANALYSIS_OP_EXPORTED_NAMES_IN_SOURCE__",
            OP_EXPORTED_NAMES_IN_SOURCE,
        ).replace(
            "__SPROUT_ANALYSIS_OP_SYMBOL_INVENTORY_IN_SOURCE__",
            OP_SYMBOL_INVENTORY_IN_SOURCE,
        ).replace(
            "__SPROUT_ANALYSIS_OP_DIAGNOSTICS_IN_SOURCE__",
            OP_DIAGNOSTICS_IN_SOURCE,
        ).replace(
            "__SPROUT_ANALYSIS_OP_SYMBOL_LOCATIONS_IN_SOURCE__",
            OP_SYMBOL_LOCATIONS_IN_SOURCE,
        ).replace(
            "__SPROUT_ANALYSIS_OP_INSTANCES_IN_SOURCE__",
            OP_INSTANCES_IN_SOURCE,
        ).replace(
            "__SPROUT_ANALYSIS_OP_COMPLETE_IN_STATE__",
            OP_COMPLETE_IN_STATE,
        )
    )


def render_analysis_bridge_request_helpers_c() -> str:
    return """
static char* sprout_analysis_request_source_only(const char* op, const char* module_source) {
  char* escaped_source = sprout_json_escape(module_source);
  size_t request_len = strlen(op) + strlen(escaped_source) + 48;
  char* request = alloc_cstr(request_len, "analysis service: out of memory");
  snprintf(
    request,
    request_len + 1,
    "{\\\"op\\\":\\\"%s\\\",\\\"module_source\\\":\\\"%s\\\"}\\n",
    op,
    escaped_source
  );
  free(escaped_source);
  return request;
}

static char* sprout_analysis_request_source_field(
  const char* op,
  const char* module_source,
  const char* field_name,
  const char* field_value
) {
  char* escaped_source = sprout_json_escape(module_source);
  char* escaped_value = sprout_json_escape(field_value);
  size_t request_len = strlen(op) + strlen(escaped_source) + strlen(field_name) + strlen(escaped_value) + 64;
  char* request = alloc_cstr(request_len, "analysis service: out of memory");
  snprintf(
    request,
    request_len + 1,
    "{\\\"op\\\":\\\"%s\\\",\\\"module_source\\\":\\\"%s\\\",\\\"%s\\\":\\\"%s\\\"}\\n",
    op,
    escaped_source,
    field_name,
    escaped_value
  );
  free(escaped_source);
  free(escaped_value);
  return request;
}
"""
