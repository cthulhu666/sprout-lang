from __future__ import annotations

from pathlib import Path

from . import ast
from .module_loader import ModuleBundle


class SurfaceCheckError(ValueError):
    pass


RAW_VECTOR_BUILTINS = {
    "vector_empty",
    "vector_length",
    "vector_get",
    "vector_set",
    "vector_append",
    "vector_sort_by_int",
}

RAW_MAP_BUILTINS = {
    "map_empty",
    "map_get",
    "map_set",
    "map_remove",
    "map_size",
    "map_nth_key",
    "map_nth_value",
}

RAW_STRING_BUILTINS = {
    "split_words",
    "str_concat",
    "str_len",
    "str_slice",
    "str_find",
    "str_starts_with",
    "str_compare",
}

RAW_BYTES_BUILTINS = {
    "bytes_empty",
    "bytes_length",
    "bytes_get",
    "bytes_slice",
    "bytes_append",
    "bytes_singleton",
    "bytes_from_utf8",
    "bytes_to_utf8",
    "bytes_builder_empty",
    "bytes_builder_bytes",
    "bytes_builder_byte",
    "bytes_builder_u16_be",
    "bytes_builder_u32_be",
    "bytes_builder_append",
    "bytes_builder_build",
}

RAW_CRYPTO_BUILTINS = {
    "crypto_sha256",
    "crypto_hmac_sha256",
    "crypto_base64_encode",
    "crypto_base64_decode",
    "crypto_bytes_xor",
    "crypto_random_bytes",
}

RAW_RANGE_BUILTINS = {
    "int_range",
    "int_range_start",
    "int_range_end",
}


def _module_path_for_line(bundle: ModuleBundle, line: int) -> Path | None:
    for segment in bundle.segments:
        if segment.start_line <= line <= segment.end_line:
            return segment.path
    return None


def _is_stdlib_module(bundle: ModuleBundle, path: Path) -> bool:
    info = bundle.modules.get(path)
    if info is not None and info.header.module is not None:
        mod = info.header.module
        if mod == "stdlib" or mod.startswith("stdlib."):
            return True
    return "stdlib" in path.parts


def _walk_type_expr(node: ast.TypeExpr) -> bool:
    if isinstance(node, ast.TypeName):
        return node.name in {"Vector", "Map"}
    if isinstance(node, ast.TypeApply):
        return _walk_type_expr(node.base) or _walk_type_expr(node.arg)
    if isinstance(node, ast.TypeArrow):
        return _walk_type_expr(node.left) or _walk_type_expr(node.right)
    return False


def _walk_expr_has_raw_vector_builtin(expr: ast.Expr) -> bool:
    if isinstance(expr, ast.VarExpr):
        return expr.name in RAW_VECTOR_BUILTINS
    if isinstance(expr, ast.UnaryExpr):
        return _walk_expr_has_raw_vector_builtin(expr.operand)
    if isinstance(expr, ast.IntRangeExpr):
        return _walk_expr_has_raw_vector_builtin(expr.start) or _walk_expr_has_raw_vector_builtin(expr.end)
    if isinstance(expr, ast.BinaryExpr):
        return _walk_expr_has_raw_vector_builtin(expr.left) or _walk_expr_has_raw_vector_builtin(expr.right)
    if isinstance(expr, ast.CallExpr):
        if _walk_expr_has_raw_vector_builtin(expr.callee):
            return True
        return any(_walk_expr_has_raw_vector_builtin(arg) for arg in expr.args)
    if isinstance(expr, ast.IfExpr):
        return (
            _walk_expr_has_raw_vector_builtin(expr.condition)
            or _walk_expr_has_raw_vector_builtin(expr.then_branch)
            or _walk_expr_has_raw_vector_builtin(expr.else_branch)
        )
    if isinstance(expr, ast.MatchExpr):
        if _walk_expr_has_raw_vector_builtin(expr.scrutinee):
            return True
        return any(_walk_expr_has_raw_vector_builtin(branch.value) for branch in expr.branches)
    return False


def _walk_expr_has_raw_map_builtin(expr: ast.Expr) -> bool:
    if isinstance(expr, ast.VarExpr):
        return expr.name in RAW_MAP_BUILTINS
    if isinstance(expr, ast.UnaryExpr):
        return _walk_expr_has_raw_map_builtin(expr.operand)
    if isinstance(expr, ast.IntRangeExpr):
        return _walk_expr_has_raw_map_builtin(expr.start) or _walk_expr_has_raw_map_builtin(expr.end)
    if isinstance(expr, ast.BinaryExpr):
        return _walk_expr_has_raw_map_builtin(expr.left) or _walk_expr_has_raw_map_builtin(expr.right)
    if isinstance(expr, ast.CallExpr):
        if _walk_expr_has_raw_map_builtin(expr.callee):
            return True
        return any(_walk_expr_has_raw_map_builtin(arg) for arg in expr.args)
    if isinstance(expr, ast.IfExpr):
        return (
            _walk_expr_has_raw_map_builtin(expr.condition)
            or _walk_expr_has_raw_map_builtin(expr.then_branch)
            or _walk_expr_has_raw_map_builtin(expr.else_branch)
        )
    if isinstance(expr, ast.MatchExpr):
        if _walk_expr_has_raw_map_builtin(expr.scrutinee):
            return True
        return any(_walk_expr_has_raw_map_builtin(branch.value) for branch in expr.branches)
    return False


def _walk_expr_has_raw_string_builtin(expr: ast.Expr) -> bool:
    if isinstance(expr, ast.VarExpr):
        return expr.name in RAW_STRING_BUILTINS
    if isinstance(expr, ast.UnaryExpr):
        return _walk_expr_has_raw_string_builtin(expr.operand)
    if isinstance(expr, ast.IntRangeExpr):
        return _walk_expr_has_raw_string_builtin(expr.start) or _walk_expr_has_raw_string_builtin(expr.end)
    if isinstance(expr, ast.BinaryExpr):
        return _walk_expr_has_raw_string_builtin(expr.left) or _walk_expr_has_raw_string_builtin(expr.right)
    if isinstance(expr, ast.CallExpr):
        if _walk_expr_has_raw_string_builtin(expr.callee):
            return True
        return any(_walk_expr_has_raw_string_builtin(arg) for arg in expr.args)
    if isinstance(expr, ast.IfExpr):
        return (
            _walk_expr_has_raw_string_builtin(expr.condition)
            or _walk_expr_has_raw_string_builtin(expr.then_branch)
            or _walk_expr_has_raw_string_builtin(expr.else_branch)
        )
    if isinstance(expr, ast.MatchExpr):
        if _walk_expr_has_raw_string_builtin(expr.scrutinee):
            return True
        return any(_walk_expr_has_raw_string_builtin(branch.value) for branch in expr.branches)
    return False


def _walk_expr_has_raw_bytes_builtin(expr: ast.Expr) -> bool:
    if isinstance(expr, ast.VarExpr):
        return expr.name in RAW_BYTES_BUILTINS
    if isinstance(expr, ast.UnaryExpr):
        return _walk_expr_has_raw_bytes_builtin(expr.operand)
    if isinstance(expr, ast.IntRangeExpr):
        return _walk_expr_has_raw_bytes_builtin(expr.start) or _walk_expr_has_raw_bytes_builtin(expr.end)
    if isinstance(expr, ast.BinaryExpr):
        return _walk_expr_has_raw_bytes_builtin(expr.left) or _walk_expr_has_raw_bytes_builtin(expr.right)
    if isinstance(expr, ast.CallExpr):
        if _walk_expr_has_raw_bytes_builtin(expr.callee):
            return True
        return any(_walk_expr_has_raw_bytes_builtin(arg) for arg in expr.args)
    if isinstance(expr, ast.IfExpr):
        return (
            _walk_expr_has_raw_bytes_builtin(expr.condition)
            or _walk_expr_has_raw_bytes_builtin(expr.then_branch)
            or _walk_expr_has_raw_bytes_builtin(expr.else_branch)
        )
    if isinstance(expr, ast.MatchExpr):
        if _walk_expr_has_raw_bytes_builtin(expr.scrutinee):
            return True
        return any(_walk_expr_has_raw_bytes_builtin(branch.value) for branch in expr.branches)
    return False


def _walk_expr_has_raw_crypto_builtin(expr: ast.Expr) -> bool:
    if isinstance(expr, ast.VarExpr):
        return expr.name in RAW_CRYPTO_BUILTINS
    if isinstance(expr, ast.UnaryExpr):
        return _walk_expr_has_raw_crypto_builtin(expr.operand)
    if isinstance(expr, ast.IntRangeExpr):
        return _walk_expr_has_raw_crypto_builtin(expr.start) or _walk_expr_has_raw_crypto_builtin(expr.end)
    if isinstance(expr, ast.BinaryExpr):
        return _walk_expr_has_raw_crypto_builtin(expr.left) or _walk_expr_has_raw_crypto_builtin(expr.right)
    if isinstance(expr, ast.CallExpr):
        if _walk_expr_has_raw_crypto_builtin(expr.callee):
            return True
        return any(_walk_expr_has_raw_crypto_builtin(arg) for arg in expr.args)
    if isinstance(expr, ast.IfExpr):
        return (
            _walk_expr_has_raw_crypto_builtin(expr.condition)
            or _walk_expr_has_raw_crypto_builtin(expr.then_branch)
            or _walk_expr_has_raw_crypto_builtin(expr.else_branch)
        )
    if isinstance(expr, ast.MatchExpr):
        if _walk_expr_has_raw_crypto_builtin(expr.scrutinee):
            return True
        return any(_walk_expr_has_raw_crypto_builtin(branch.value) for branch in expr.branches)
    return False


def _walk_expr_has_raw_range_builtin(expr: ast.Expr) -> bool:
    if isinstance(expr, ast.VarExpr):
        return expr.name in RAW_RANGE_BUILTINS
    if isinstance(expr, ast.UnaryExpr):
        return _walk_expr_has_raw_range_builtin(expr.operand)
    if isinstance(expr, ast.IntRangeExpr):
        return _walk_expr_has_raw_range_builtin(expr.start) or _walk_expr_has_raw_range_builtin(expr.end)
    if isinstance(expr, ast.BinaryExpr):
        return _walk_expr_has_raw_range_builtin(expr.left) or _walk_expr_has_raw_range_builtin(expr.right)
    if isinstance(expr, ast.CallExpr):
        if _walk_expr_has_raw_range_builtin(expr.callee):
            return True
        return any(_walk_expr_has_raw_range_builtin(arg) for arg in expr.args)
    if isinstance(expr, ast.IfExpr):
        return (
            _walk_expr_has_raw_range_builtin(expr.condition)
            or _walk_expr_has_raw_range_builtin(expr.then_branch)
            or _walk_expr_has_raw_range_builtin(expr.else_branch)
        )
    if isinstance(expr, ast.MatchExpr):
        if _walk_expr_has_raw_range_builtin(expr.scrutinee):
            return True
        return any(_walk_expr_has_raw_range_builtin(branch.value) for branch in expr.branches)
    return False


def _ensure_allowed(bundle: ModuleBundle, node: object, reason: str) -> None:
    return


def validate_public_surface(program: ast.Program, bundle: ModuleBundle | None) -> None:
    if bundle is None:
        return

    for decl in program.declarations:
        if isinstance(decl, ast.TypeDecl):
            for ctor in decl.constructors:
                for arg in ctor.args:
                    if _walk_type_expr(arg):
                        _ensure_allowed(bundle, arg, "Vector/Map type")
        elif isinstance(decl, ast.FnDecl):
            for param in decl.params:
                if param.type_expr is not None and _walk_type_expr(param.type_expr):
                    _ensure_allowed(bundle, param.type_expr, "Vector/Map type")
            if decl.return_type is not None and _walk_type_expr(decl.return_type):
                _ensure_allowed(bundle, decl.return_type, "Vector/Map type")
            for constraint in decl.constraints:
                for arg in constraint.args:
                    if _walk_type_expr(arg):
                        _ensure_allowed(bundle, arg, "Vector/Map type")
            if _walk_expr_has_raw_vector_builtin(decl.body):
                _ensure_allowed(bundle, decl.body, "vector_* builtin")
            if _walk_expr_has_raw_map_builtin(decl.body):
                _ensure_allowed(bundle, decl.body, "map_* builtin")
            if _walk_expr_has_raw_string_builtin(decl.body):
                _ensure_allowed(bundle, decl.body, "string builtin")
            if _walk_expr_has_raw_bytes_builtin(decl.body):
                _ensure_allowed(bundle, decl.body, "bytes_* builtin")
            if _walk_expr_has_raw_crypto_builtin(decl.body):
                _ensure_allowed(bundle, decl.body, "crypto_* builtin")
            if _walk_expr_has_raw_range_builtin(decl.body):
                _ensure_allowed(bundle, decl.body, "int_range* builtin")
        elif isinstance(decl, ast.LetDecl):
            if _walk_expr_has_raw_vector_builtin(decl.value):
                _ensure_allowed(bundle, decl.value, "vector_* builtin")
            if _walk_expr_has_raw_map_builtin(decl.value):
                _ensure_allowed(bundle, decl.value, "map_* builtin")
            if _walk_expr_has_raw_string_builtin(decl.value):
                _ensure_allowed(bundle, decl.value, "string builtin")
            if _walk_expr_has_raw_bytes_builtin(decl.value):
                _ensure_allowed(bundle, decl.value, "bytes_* builtin")
            if _walk_expr_has_raw_crypto_builtin(decl.value):
                _ensure_allowed(bundle, decl.value, "crypto_* builtin")
            if _walk_expr_has_raw_range_builtin(decl.value):
                _ensure_allowed(bundle, decl.value, "int_range* builtin")
        elif isinstance(decl, ast.ClassDecl):
            for method in decl.methods:
                for param in method.params:
                    if param.type_expr is not None and _walk_type_expr(param.type_expr):
                        _ensure_allowed(bundle, param.type_expr, "Vector/Map type")
                if _walk_type_expr(method.return_type):
                    _ensure_allowed(bundle, method.return_type, "Vector/Map type")
        elif isinstance(decl, ast.InstanceDecl):
            for arg in decl.constraint.args:
                if _walk_type_expr(arg):
                    _ensure_allowed(bundle, arg, "Vector/Map type")
            for method in decl.methods:
                for param in method.params:
                    if param.type_expr is not None and _walk_type_expr(param.type_expr):
                        _ensure_allowed(bundle, param.type_expr, "Vector/Map type")
                if method.return_type is not None and _walk_type_expr(method.return_type):
                    _ensure_allowed(bundle, method.return_type, "Vector/Map type")
                if _walk_expr_has_raw_vector_builtin(method.body):
                    _ensure_allowed(bundle, method.body, "vector_* builtin")
                if _walk_expr_has_raw_map_builtin(method.body):
                    _ensure_allowed(bundle, method.body, "map_* builtin")
                if _walk_expr_has_raw_string_builtin(method.body):
                    _ensure_allowed(bundle, method.body, "string builtin")
                if _walk_expr_has_raw_bytes_builtin(method.body):
                    _ensure_allowed(bundle, method.body, "bytes_* builtin")
                if _walk_expr_has_raw_crypto_builtin(method.body):
                    _ensure_allowed(bundle, method.body, "crypto_* builtin")
                if _walk_expr_has_raw_range_builtin(method.body):
                    _ensure_allowed(bundle, method.body, "int_range* builtin")
