from __future__ import annotations

import json

from .analysis_service_config import (
    analysis_service_empty_response_error,
    analysis_service_env_var_name,
    analysis_service_invalid_response_error,
    analysis_service_request_failed_error,
    render_analysis_service_retry_allowed_c,
    analysis_service_start_error,
)

__all__ = [
    "render_analysis_bridge_helpers_c",
    "render_analysis_bridge_request_helpers_c",
    "render_analysis_bridge_response_helpers_c",
    "render_analysis_bridge_runtime_c",
]


def render_analysis_bridge_helpers_c() -> str:
    return (
        render_analysis_bridge_request_helpers_c()
        + render_analysis_bridge_response_helpers_c()
    ).replace(
        "__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__",
        analysis_service_invalid_response_error(),
    )


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
"""
        + render_analysis_service_retry_allowed_c()
    ).replace(
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


def render_analysis_bridge_response_helpers_c() -> str:
    return """
static long long sprout_analysis_error_from_response(char* response) {
  char* error = sprout_json_extract_string(response, "error");
  free(response);
  long long out = sprout_err_string_result(error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  if (error != NULL) free(error);
  return out;
}

static long long sprout_analysis_ok_string_result_from_response(char* response, const char* value_key) {
  char* value = sprout_json_extract_string(response, value_key);
  free(response);
  if (value == NULL) return sprout_err_string_result("__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  return sprout_make1(find_ctor_tag_by_name("Ok"), (long long)(uintptr_t)value);
}

static long long sprout_analysis_ok_vec_string_result(VectorVal* items) {
  if (items == NULL) return sprout_err_string_result("__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  long long rooted_items = (long long)(uintptr_t)items;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_items);
  long long items_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_items);
  SPROUT_GC_PUSH_I64_LOCAL(items_vec);
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), items_vec);
  SPROUT_GC_POP_LOCALS(2);
  return out;
}

static long long sprout_analysis_ok_vec_string_result_from_response(char* response, const char* value_key) {
  VectorVal* items = sprout_json_extract_string_array(response, value_key);
  free(response);
  return sprout_analysis_ok_vec_string_result(items);
}

static long long sprout_analysis_ok_string_vec_pair_result(char* label, VectorVal* items) {
  if (label == NULL || items == NULL) return sprout_err_string_result("__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  long long rooted_items = (long long)(uintptr_t)items;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_items);
  long long items_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_items);
  SPROUT_GC_PUSH_I64_LOCAL(items_vec);
  void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 2));
  uintptr_t* words = (uintptr_t*)tuple;
  words[0] = (uintptr_t)label;
  words[1] = (uintptr_t)items_vec;
  SPROUT_GC_POP_LOCALS(2);
  long long pair = (long long)(uintptr_t)tuple;
  return sprout_make1(find_ctor_tag_by_name("Ok"), pair);
}

static long long sprout_analysis_ok_string_vec_pair_from_response(
  char* response,
  const char* string_key,
  const char* array_key
) {
  char* label = sprout_json_extract_string(response, string_key);
  VectorVal* items = sprout_json_extract_string_array(response, array_key);
  free(response);
  return sprout_analysis_ok_string_vec_pair_result(label, items);
}

static long long sprout_analysis_completion_tuple_or_fail(
  const char* builtin_name,
  char* response,
  const char* string_key,
  const char* array_key
) {
  char* prefix = sprout_json_extract_string(response, string_key);
  VectorVal* matches = sprout_json_extract_string_array(response, array_key);
  free(response);
  if (prefix == NULL || matches == NULL) {
    sprout_builtin_fail_detail(builtin_name, "__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  }
  long long rooted_matches = (long long)(uintptr_t)matches;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_matches);
  long long matches_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_matches);
  SPROUT_GC_PUSH_I64_LOCAL(matches_vec);
  void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 2));
  uintptr_t* words = (uintptr_t*)tuple;
  words[0] = (uintptr_t)prefix;
  words[1] = (uintptr_t)matches_vec;
  SPROUT_GC_POP_LOCALS(2);
  return (long long)(uintptr_t)tuple;
}

static long long sprout_analysis_ok_inventory_result(
  VectorVal* declared,
  VectorVal* imported,
  VectorVal* exported
) {
  if (declared == NULL || imported == NULL || exported == NULL) {
    return sprout_err_string_result("__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  }
  long long rooted_declared = (long long)(uintptr_t)declared;
  long long rooted_imported = (long long)(uintptr_t)imported;
  long long rooted_exported = (long long)(uintptr_t)exported;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_declared);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_imported);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_exported);
  long long declared_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_declared);
  long long imported_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_imported);
  long long exported_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_exported);
  SPROUT_GC_PUSH_I64_LOCAL(declared_vec);
  SPROUT_GC_PUSH_I64_LOCAL(imported_vec);
  SPROUT_GC_PUSH_I64_LOCAL(exported_vec);
  void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 3));
  uintptr_t* words = (uintptr_t*)tuple;
  words[0] = (uintptr_t)declared_vec;
  words[1] = (uintptr_t)imported_vec;
  words[2] = (uintptr_t)exported_vec;
  SPROUT_GC_POP_LOCALS(6);
  return sprout_make1(find_ctor_tag_by_name("Ok"), (long long)(uintptr_t)tuple);
}

static long long sprout_analysis_ok_inventory_from_response(
  char* response,
  const char* declared_key,
  const char* imported_key,
  const char* exported_key
) {
  VectorVal* declared = sprout_json_extract_string_array(response, declared_key);
  VectorVal* imported = sprout_json_extract_string_array(response, imported_key);
  VectorVal* exported = sprout_json_extract_string_array(response, exported_key);
  free(response);
  return sprout_analysis_ok_inventory_result(declared, imported, exported);
}

static long long sprout_analysis_diagnostics_vec_or_fail(
  const char* builtin_name,
  char* response,
  const char* messages_key,
  const char* lines_key,
  const char* columns_key
) {
  VectorVal* messages = sprout_json_extract_string_array(response, messages_key);
  long long line_count = 0;
  long long column_count = 0;
  long long* lines = sprout_json_extract_int_array(response, lines_key, &line_count);
  long long* columns = sprout_json_extract_int_array(response, columns_key, &column_count);
  free(response);
  if (
    messages == NULL ||
    line_count != messages->len ||
    column_count != messages->len ||
    (messages->len > 0 && (lines == NULL || columns == NULL))
  ) {
    if (lines != NULL) free(lines);
    if (columns != NULL) free(columns);
    sprout_builtin_fail_detail(builtin_name, "__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  }
  VectorVal* out = sprout_alloc_vector_val("analysis service: out of memory");
  long long rooted_messages = (long long)(uintptr_t)messages;
  long long rooted_out = (long long)(uintptr_t)out;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_messages);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_out);
  out->len = messages->len;
  out->cap = messages->len;
  out->data = messages->len == 0 ? NULL : sprout_realloc_vector_data(NULL, (size_t)messages->len, "analysis service: out of memory");
  for (long long i = 0; i < messages->len; i++) {
    void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 3));
    uintptr_t* words = (uintptr_t*)tuple;
    words[0] = (uintptr_t)messages->data[i];
    words[1] = (uintptr_t)lines[i];
    words[2] = (uintptr_t)columns[i];
    out->data[i] = (long long)(uintptr_t)tuple;
  }
  if (lines != NULL) free(lines);
  if (columns != NULL) free(columns);
  long long out_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_out);
  SPROUT_GC_POP_LOCALS(2);
  return out_vec;
}

static long long sprout_analysis_ok_symbol_locations_from_response(
  char* response,
  const char* categories_key,
  const char* names_key,
  const char* lines_key,
  const char* columns_key
) {
  VectorVal* categories = sprout_json_extract_string_array(response, categories_key);
  VectorVal* names = sprout_json_extract_string_array(response, names_key);
  long long line_count = 0;
  long long column_count = 0;
  long long* lines = sprout_json_extract_int_array(response, lines_key, &line_count);
  long long* columns = sprout_json_extract_int_array(response, columns_key, &column_count);
  free(response);
  if (
    categories == NULL ||
    names == NULL ||
    categories->len != names->len ||
    line_count != categories->len ||
    column_count != categories->len ||
    (categories->len > 0 && (lines == NULL || columns == NULL))
  ) {
    if (lines != NULL) free(lines);
    if (columns != NULL) free(columns);
    return sprout_err_string_result("__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  }
  VectorVal* out = sprout_alloc_vector_val("analysis service: out of memory");
  long long rooted_categories = (long long)(uintptr_t)categories;
  long long rooted_names = (long long)(uintptr_t)names;
  long long rooted_out = (long long)(uintptr_t)out;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_categories);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_names);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_out);
  out->len = categories->len;
  out->cap = categories->len;
  out->data = categories->len == 0 ? NULL : sprout_realloc_vector_data(NULL, (size_t)categories->len, "analysis service: out of memory");
  for (long long i = 0; i < categories->len; i++) {
    void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 4));
    uintptr_t* words = (uintptr_t*)tuple;
    words[0] = (uintptr_t)categories->data[i];
    words[1] = (uintptr_t)names->data[i];
    words[2] = (uintptr_t)lines[i];
    words[3] = (uintptr_t)columns[i];
    out->data[i] = (long long)(uintptr_t)tuple;
  }
  if (lines != NULL) free(lines);
  if (columns != NULL) free(columns);
  long long rooted_vec_raw = (long long)(uintptr_t)out;
  long long out_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_vec_raw);
  SPROUT_GC_PUSH_I64_LOCAL(out_vec);
  long long result = sprout_make1(find_ctor_tag_by_name("Ok"), out_vec);
  SPROUT_GC_POP_LOCALS(4);
  return result;
}
"""
