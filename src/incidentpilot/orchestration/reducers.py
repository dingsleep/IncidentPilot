from __future__ import annotations

from typing import Any

from incidentpilot.domain.diagnosis import InvestigationReport
from incidentpilot.domain.events import DomainInvariantError


def merge_ids(current: list[str], incoming: list[str]) -> list[str]:
    """Merge parallel ID writes without changing first-seen order."""
    return list(dict.fromkeys([*current, *incoming]))


def merge_wave_reports(
    current: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep one immutable report for each wave/investigator pair."""
    merged = list(current)
    by_key = {_report_key(item): item for item in current}
    for item in incoming:
        key = _report_key(item)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = item
            merged.append(item)
        elif existing != item:
            raise DomainInvariantError(f"cannot overwrite wave {key[0]} {key[1]} report")
    return merged


def keep_confirmed_diagnosis(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """A confirmed diagnosis is append-only graph state."""
    if current is None:
        return incoming
    if incoming is None or incoming == current:
        return current
    raise DomainInvariantError("cannot overwrite a confirmed diagnosis")


def _report_key(item: dict[str, Any]) -> tuple[int, str]:
    report = InvestigationReport.model_validate(item["report"])
    return int(item["wave"]), report.investigator
