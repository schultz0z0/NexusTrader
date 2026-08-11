"""Safe deterministic CSV ZIP and macro-free XLSX report exports."""

from __future__ import annotations

import csv
import io
import json
import math
import ntpath
import posixpath
import re
import zipfile
from datetime import date, datetime, timezone
from typing import Any, Mapping

from openpyxl import Workbook

from nexus_trade.artifacts import canonical_json
from nexus_trade.reports import ReportSnapshot


class ExportSafetyError(ValueError):
    """Snapshot contains data that must never enter a downloadable artifact."""


_SENSITIVE = ("token", "secret", "password", "account", "credential", "api_key", "ticket", "path")
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CSV_NAMES = ("summary", "days", "champion", "trial", "indicators", "gates", "audit")
_SHEET_NAMES = ("Summary", "Days", "Champion", "Trial", "Indicators", "Gates", "Audit")
_FIXED_ZIP_TIME = (2000, 1, 1, 0, 0, 0)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _validate(value: Any, key_path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str or not key:
                raise ExportSafetyError("export keys must be non-empty strings")
            lowered = key.lower()
            if any(part in lowered for part in _SENSITIVE):
                raise ExportSafetyError(f"sensitive export field is forbidden: {key}")
            _validate(item, key_path + (key,))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate(item, key_path)
        return
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ExportSafetyError("non-finite export values are forbidden")
        return
    if isinstance(value, (date, datetime)):
        return
    if type(value) is str:
        lowered = value.lower()
        if (
            ntpath.isabs(value)
            or posixpath.isabs(value)
            or lowered.startswith("file:")
            or re.search(r"(?:token|ticket|api_key|password|secret)=", lowered)
        ):
            raise ExportSafetyError("local paths and credentials are forbidden in exports")
        return
    raise ExportSafetyError("export values must be finite JSON-compatible data")


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ExportSafetyError("datetimes must include an explicit offset")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if type(value) in (bool, int, float):
        return value
    if isinstance(value, (Mapping, list, tuple)):
        value = canonical_json(_plain(value))
    else:
        value = str(value)
    stripped = value.lstrip()
    if stripped.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


class ReportExporter:
    def _snapshot(self, report: ReportSnapshot | Mapping[str, Any]) -> tuple[dict, str | None]:
        if type(report) is ReportSnapshot:
            snapshot = _plain(report.snapshot)
            report_hash = report.report_hash
        elif isinstance(report, Mapping):
            outer = _plain(report)
            snapshot = outer.get("snapshot") if isinstance(outer.get("snapshot"), dict) else outer
            report_hash = outer.get("report_hash")
        else:
            raise TypeError("report must be a ReportSnapshot or mapping")
        _validate(snapshot)
        required = {"window", "champion", "trial", "diffs", "gates", "audit"}
        if not isinstance(snapshot, dict) or not required.issubset(snapshot):
            raise ExportSafetyError("report snapshot is incomplete")
        return snapshot, report_hash

    def filename(self, report: ReportSnapshot | Mapping[str, Any], format_name: str) -> str:
        snapshot, report_hash = self._snapshot(report)
        if format_name not in {"csv_zip", "xlsx"}:
            raise ValueError("format must be csv_zip or xlsx")
        if not isinstance(report_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", report_hash):
            import hashlib
            report_hash = hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
        extension = "csv.zip" if format_name == "csv_zip" else "xlsx"
        return f"nexus-report-{report_hash[:24]}.{extension}"

    def csv_zip(self, report: ReportSnapshot | Mapping[str, Any]) -> bytes:
        snapshot, _ = self._snapshot(report)
        tables = self._tables(snapshot)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in _CSV_NAMES:
                info = zipfile.ZipInfo(f"{name}.csv", _FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, self._csv(tables[name]))
        return output.getvalue()

    def xlsx(self, report: ReportSnapshot | Mapping[str, Any]) -> bytes:
        snapshot, _ = self._snapshot(report)
        tables = self._tables(snapshot)
        workbook = Workbook(write_only=False)
        workbook.remove(workbook.active)
        fixed = datetime(2000, 1, 1, tzinfo=timezone.utc)
        workbook.properties.created = fixed
        workbook.properties.modified = fixed
        workbook.properties.creator = "NexusTrade"
        workbook.properties.lastModifiedBy = "NexusTrade"
        for sheet_name, table_name in zip(_SHEET_NAMES, _CSV_NAMES):
            sheet = workbook.create_sheet(sheet_name)
            rows = tables[table_name]
            headers = self._headers(rows)
            sheet.append(headers)
            for row in rows:
                sheet.append([_cell(row.get(header)) for header in headers])
            sheet.freeze_panes = "A2"
        raw = io.BytesIO()
        workbook.save(raw)
        return self._normalize_zip(raw.getvalue())

    @staticmethod
    def _tables(snapshot: dict) -> dict[str, list[dict]]:
        summary_values = {
            "schema_version": snapshot.get("schema_version"),
            "report_type": snapshot.get("report_type"),
            "campaign_id": snapshot.get("campaign_id"),
            "window_start_utc": snapshot["window"].get("start_utc"),
            "window_end_utc": snapshot["window"].get("end_utc"),
            "window_start_local": snapshot["window"].get("start_local"),
            "window_end_local": snapshot["window"].get("end_local"),
            "recommendation": snapshot.get("recommendation"),
            "recommendation_reasons": snapshot.get("recommendation_reasons"),
            "accumulated_progress": snapshot.get("accumulated_progress"),
            "disclosure": snapshot.get("disclosure"),
        }
        tables = {
            "summary": [{"field": key, "value": value} for key, value in summary_values.items()],
            "days": [dict(item) for item in snapshot.get("days", [])],
            "champion": ReportExporter._lane_rows(snapshot["champion"]),
            "trial": ReportExporter._lane_rows(snapshot["trial"]),
            "indicators": ReportExporter._indicator_rows(snapshot["diffs"]),
            "gates": [dict(item) for item in snapshot.get("gates", [])],
            "audit": [dict(item) for item in snapshot.get("audit", [])],
        }
        return tables

    @staticmethod
    def _lane_rows(lane: dict) -> list[dict]:
        rows = [
            {"field": key, "value": value}
            for key, value in lane.items()
            if key != "metrics"
        ]
        rows.extend({"field": f"metrics.{key}", "value": value} for key, value in lane.get("metrics", {}).items())
        return rows

    @staticmethod
    def _indicator_rows(diffs: dict) -> list[dict]:
        rows = []
        indicators = diffs.get("indicators", {})
        for change in ("added", "removed", "reconfigured"):
            rows.extend({"change": change, "indicator": name} for name in indicators.get(change, []))
        rows.extend(
            {"change": "feature_added", "indicator": name}
            for name in diffs.get("features", {}).get("added", [])
        )
        rows.extend(
            {"change": "feature_removed", "indicator": name}
            for name in diffs.get("features", {}).get("removed", [])
        )
        rows.append({"change": "entry_rules", "indicator": diffs.get("entry_rules")})
        rows.append({"change": "model", "indicator": diffs.get("model")})
        return rows

    @staticmethod
    def _headers(rows: list[dict]) -> list[str]:
        headers = []
        for row in rows:
            for key in row:
                if key not in headers:
                    headers.append(key)
        return headers or ["empty"]

    @classmethod
    def _csv(cls, rows: list[dict]) -> bytes:
        text = io.StringIO(newline="")
        headers = cls._headers(rows)
        writer = csv.DictWriter(text, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: _cell(row.get(header)) for header in headers})
        return ("\ufeff" + text.getvalue()).encode("utf-8")

    @staticmethod
    def _normalize_zip(payload: bytes) -> bytes:
        source = zipfile.ZipFile(io.BytesIO(payload), "r")
        output = io.BytesIO()
        with source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
            for name in sorted(source.namelist()):
                if name.lower().endswith(("vbaproject.bin", ".vba")):
                    raise ExportSafetyError("executable macros are forbidden")
                info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                target.writestr(info, source.read(name))
        return output.getvalue()


__all__ = ["ExportSafetyError", "ReportExporter"]
