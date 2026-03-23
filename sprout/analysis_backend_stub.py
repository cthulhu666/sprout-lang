from __future__ import annotations

from dataclasses import dataclass, field

from .analysis_backend import AnalysisBackend

__all__ = ["StubAnalysisBackend"]


@dataclass
class StubAnalysisBackend(AnalysisBackend):
    check_source_result: None = None
    type_of_result: str | None = None
    declared_names_result: list[str] | None = None
    exported_names_result: list[str] | None = None
    symbol_inventory_result: tuple[list[str], list[str], list[str]] | None = None
    diagnostics_result: list[tuple[str, int, int]] | None = None
    symbol_locations_result: list[tuple[str, str, int, int]] | None = None
    instances_result: tuple[str, list[str]] | None = None
    eval_expr_result: tuple[str, ...] | None = None
    complete_in_state_result: tuple[str, list[str]] | None = None
    seen_calls: list[tuple[str, object]] = field(default_factory=list)

    def _record(self, name: str, payload: object) -> None:
        self.seen_calls.append((name, payload))

    def _missing(self, name: str) -> AssertionError:
        return AssertionError(f"stub backend missing configured result for {name}")

    def check_source(self, module_source: str) -> None:
        self._record("check_source", module_source)
        if self.check_source_result is None:
            return

    def type_of_in_source(self, module_source: str, expr: str) -> str:
        self._record("type_of_in_source", (module_source, expr))
        if self.type_of_result is None:
            raise self._missing("type_of_in_source")
        return self.type_of_result

    def declared_names_in_source(self, module_source: str) -> list[str]:
        self._record("declared_names_in_source", module_source)
        if self.declared_names_result is None:
            raise self._missing("declared_names_in_source")
        return self.declared_names_result

    def exported_names_in_source(self, module_source: str) -> list[str]:
        self._record("exported_names_in_source", module_source)
        if self.exported_names_result is None:
            raise self._missing("exported_names_in_source")
        return self.exported_names_result

    def symbol_inventory_in_source(self, module_source: str) -> tuple[list[str], list[str], list[str]]:
        self._record("symbol_inventory_in_source", module_source)
        if self.symbol_inventory_result is None:
            raise self._missing("symbol_inventory_in_source")
        return self.symbol_inventory_result

    def diagnostics_in_source(self, module_source: str) -> list[tuple[str, int, int]]:
        self._record("diagnostics_in_source", module_source)
        if self.diagnostics_result is None:
            raise self._missing("diagnostics_in_source")
        return self.diagnostics_result

    def symbol_locations_in_source(self, module_source: str) -> list[tuple[str, str, int, int]]:
        self._record("symbol_locations_in_source", module_source)
        if self.symbol_locations_result is None:
            raise self._missing("symbol_locations_in_source")
        return self.symbol_locations_result

    def instances_in_source(self, module_source: str, query: str) -> tuple[str, list[str]]:
        self._record("instances_in_source", (module_source, query))
        if self.instances_result is None:
            raise self._missing("instances_in_source")
        return self.instances_result

    def eval_expr_in_source(self, module_source: str, expr: str) -> tuple[str, ...]:
        self._record("eval_expr_in_source", (module_source, expr))
        if self.eval_expr_result is None:
            raise self._missing("eval_expr_in_source")
        return self.eval_expr_result

    def complete_in_state(
        self,
        line_buffer: str,
        imports: list[str],
        declarations: list[str],
    ) -> tuple[str, list[str]]:
        self._record("complete_in_state", (line_buffer, imports, declarations))
        if self.complete_in_state_result is None:
            raise self._missing("complete_in_state")
        return self.complete_in_state_result
