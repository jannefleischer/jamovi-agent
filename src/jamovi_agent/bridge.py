"""Thin layer over jamovi server internals.

Everything that touches jamovi's private API lives here, so a jamovi update
only has to be checked against this file. Pinned against jamovi 28.18:

- ``Session`` (dict of instance id -> ``Instance``), ``session.modules``
- ``Instance.project`` / ``.analyses`` / ``._coms`` / ``.is_active``
- ``Project.get_dataset()``, ``Project.is_edited``
- ``Analyses.create()``, ``.create_annotation()``, ``.has_header_annotation()``,
  ``.index_of()``, ``.get()``, iteration
- ``Analysis.options`` / ``.results`` / ``.status`` / ``.complete`` /
  ``.set_options()`` / ``.run()`` / ``.dependents``
- ``jamovi_pb2`` messages ``AnalysisOptions`` and ``AnalysisResponse``
- ``jamovi.server.options.write_value_to_pb`` / ``read_values_from_pb``

The browser keeps its single websocket connection. Analyses created here get
odd ids from ``Analyses.create(0, ...)``; the client treats an unknown odd id
in an ``AnalysisResponse`` as a new server-side analysis and renders it.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable

log = logging.getLogger(__name__)


class BridgeError(Exception):
    """A request the bridge cannot fulfil (unknown instance, analysis, ...)."""


def _jcoms():
    from jamovi.server import jamovi_pb2
    return jamovi_pb2


def _enum_name(value) -> str:
    name = getattr(value, "name", None)
    return name.lower() if isinstance(name, str) else str(value).lower()


@dataclass
class ColumnInfo:
    """Summary of one data set column, as shown to the LLM."""

    name: str
    measure_type: str
    data_type: str
    column_type: str
    description: str
    levels: list[str]
    formula: str

    def as_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "measure_type": self.measure_type,
            "data_type": self.data_type,
            "column_type": self.column_type,
        }
        if self.description:
            d["description"] = self.description
        if self.levels:
            d["levels"] = self.levels
        if self.formula:
            d["formula"] = self.formula
        return d


def _level_label(level) -> str:
    for attr in ("label", "import_value"):
        v = getattr(level, attr, None)
        if v is not None:
            return str(v)
    if isinstance(level, (tuple, list)) and len(level) > 1:
        return str(level[1])
    return str(level)


def _clean_value(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if v == -2147483648:  # jamovi's missing integer
        return None
    return v


class Bridge:
    """Access to one jamovi ``Session`` and its instances."""

    def __init__(self, session):
        self._session = session

    # ----- instances -------------------------------------------------------

    def instances(self) -> list:
        """All instances of the session, active (browser connected) first."""
        insts = list(self._session.values())
        insts.sort(key=lambda i: not getattr(i, "is_active", False))
        return insts

    def instance(self, instance_id: str | None = None):
        """The instance with ``instance_id``; default: the single active one."""
        if instance_id:
            try:
                return self._session[instance_id]
            except Exception as e:
                raise BridgeError(f"no such instance: {instance_id}") from e
        active = [i for i in self._session.values() if getattr(i, "is_active", False)]
        if len(active) == 1:
            return active[0]
        if not active:
            raise BridgeError("no jamovi window is open")
        raise BridgeError("several jamovi windows are open; pass instance_id")

    def instance_summary(self, inst) -> dict[str, Any]:
        project = inst.project
        summary = {
            "instance_id": inst.id,
            "active": bool(getattr(inst, "is_active", False)),
            "title": getattr(project, "title", ""),
            "path": getattr(project, "path", ""),
        }
        try:
            ds = project.get_dataset()
            summary["rows"] = ds.row_count
            summary["columns"] = len([c for c in ds if self._is_visible(c)])
        except Exception:
            summary["rows"] = summary["columns"] = 0
        summary["analyses"] = len(self.analyses(inst))
        return summary

    # ----- data set --------------------------------------------------------

    @staticmethod
    def _is_visible(column) -> bool:
        return _enum_name(column.column_type) != "none"

    def columns(self, inst) -> list[ColumnInfo]:
        ds = inst.project.get_dataset()
        out = []
        for col in ds:
            if not self._is_visible(col):
                continue
            try:
                levels = [_level_label(lv) for lv in (col.levels or ())]
            except Exception:
                levels = []
            out.append(ColumnInfo(
                name=col.name,
                measure_type=_enum_name(col.measure_type),
                data_type=_enum_name(col.data_type),
                column_type=_enum_name(col.column_type),
                description=getattr(col, "description", "") or "",
                levels=levels,
                formula=getattr(col, "formula", "") or "",
            ))
        return out

    def column(self, inst, name: str):
        ds = inst.project.get_dataset()
        try:
            return ds[name]
        except Exception as e:
            raise BridgeError(f"no such variable: {name}") from e

    def row_count(self, inst) -> int:
        return inst.project.get_dataset().row_count

    def values(self, inst, name: str, start: int = 0, count: int | None = None) -> list:
        """Values of one column as labels/numbers (missing -> None)."""
        col = self.column(inst, name)
        n = col.row_count
        end = n if count is None else min(n, start + count)
        has_levels = getattr(col, "has_levels", False)
        has_levels = bool(has_levels() if callable(has_levels) else has_levels)
        out = []
        for i in range(start, end):
            v = _clean_value(col.get_value(i))
            if v is not None and has_levels and _enum_name(col.data_type) == "integer":
                try:
                    v = col.get_label(v)
                except Exception:
                    pass
            if v == "":
                v = None
            out.append(v)
        return out

    # ----- module catalogue --------------------------------------------------

    def catalogue(self) -> Iterable[tuple[Any, Any]]:
        """(module_meta, analysis_meta) for every analysis in visible modules."""
        for module in self._session.modules:
            if not getattr(module, "visible", True) or getattr(module, "incompatible", False):
                continue
            for analysis in module.analyses:
                yield module, analysis

    def analysis_meta(self, ns: str, name: str):
        try:
            module = self._session.modules.get(ns)
            meta = module.get(name)
        except Exception as e:
            raise BridgeError(f"no such analysis: {ns}::{name}") from e
        if meta is None:
            raise BridgeError(f"no such analysis: {ns}::{name}")
        return meta

    # ----- analyses ----------------------------------------------------------

    def analyses(self, inst) -> list:
        """User-visible analyses of an instance (annotations excluded)."""
        return [a for a in inst.analyses
                if a.name not in ("empty", "weights") and a.status.name != "DELETED"]

    def analysis(self, inst, analysis_id: int):
        a = inst.analyses.get(int(analysis_id))
        if a is None or a.name == "empty":
            raise BridgeError(f"no such analysis: {analysis_id}")
        return a

    @staticmethod
    def options_dict(analysis) -> dict[str, Any]:
        from jamovi.server.options import read_values_from_pb
        return read_values_from_pb(analysis.options.as_pb())

    @staticmethod
    def options_pb(values: dict[str, Any]):
        from jamovi.server.options import write_value_to_pb
        pb = _jcoms().AnalysisOptions()
        pb.hasNames = True
        for name, value in values.items():
            pb.names.append(name)
            write_value_to_pb(value, pb.options.add())
        return pb

    def _send(self, inst, message):
        coms = getattr(inst, "_coms", None)
        if coms is not None:
            coms.send(message, inst.id)

    def create_analysis(self, inst, ns: str, name: str, values: dict[str, Any]):
        """Create and run an analysis at the end of the results; returns it."""
        jcoms = _jcoms()
        self.analysis_meta(ns, name)  # validates ns/name
        analyses = inst.analyses

        if not analyses.has_header_annotation():
            header = analyses.create_annotation(0)
            header.results.index = 1
            header.results.title = "Results"
            self._send(inst, header.results)

        analysis = analyses.create(0, name, ns, self.options_pb(values), None)
        inst.project.is_edited = True

        index = analyses.index_of(analysis) + 1
        response = jcoms.AnalysisResponse()
        response.name = name
        response.ns = ns
        response.instanceId = inst.id
        response.analysisId = analysis.id
        response.options.ParseFromString(analysis.options.as_bytes())
        response.index = index
        response.status = jcoms.AnalysisStatus.Value("ANALYSIS_NONE")
        self._send(inst, response)

        analysis.run()

        child_index = index + 1
        for child in analysis.dependents:
            if child.results is not None:
                child.results.index = child_index
                self._send(inst, child.results)
            child_index += 1
        return analysis

    def update_analysis(self, inst, analysis_id: int, values: dict[str, Any]):
        """Change options of an analysis and re-run it."""
        analysis = self.analysis(inst, analysis_id)
        merged = self.options_dict(analysis)
        merged.update(values)
        analysis.set_options(self.options_pb(merged), list(values.keys()),
                             analysis.revision + 1, True)
        inst.project.is_edited = True
        return analysis

    async def wait_complete(self, analysis, timeout: float = 120.0) -> str:
        """Wait until an analysis has finished; returns its status name."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            status = analysis.status.name
            if analysis.complete and status in ("COMPLETE", "ERROR"):
                return status
            await asyncio.sleep(0.25)
        return "TIMEOUT"
