from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any, cast

import yaml

from incidentpilot.telemetry.schemas import (
    LogTemplate,
    LogTemplateFile,
    MetricRenderRequest,
    MetricTemplate,
    MetricTemplateFile,
)


class QueryRegistry:
    def __init__(
        self,
        *,
        metrics: list[MetricTemplate],
        logs: list[LogTemplate],
        allowed_services: set[str],
    ) -> None:
        all_ids = [template.id for template in [*metrics, *logs]]
        duplicate = next(
            (template_id for template_id in all_ids if all_ids.count(template_id) > 1),
            None,
        )
        if duplicate:
            raise ValueError(f"duplicate template id: {duplicate}")
        if not allowed_services:
            raise ValueError("at least one service must be registered")
        self._metrics = {template.id: template for template in metrics}
        self._logs = {template.id: template for template in logs}
        self._allowed_services = frozenset(allowed_services)

    @property
    def metric_ids(self) -> frozenset[str]:
        return frozenset(self._metrics)

    @property
    def log_ids(self) -> frozenset[str]:
        return frozenset(self._logs)

    @property
    def allowed_services(self) -> frozenset[str]:
        return self._allowed_services

    def metric_unit(self, template_id: str) -> str:
        try:
            return self._metrics[template_id].unit
        except KeyError as exc:
            raise ValueError(f"unknown metric template: {template_id}") from exc

    def log_index(self, template_id: str) -> str:
        try:
            return self._logs[template_id].index
        except KeyError as exc:
            raise ValueError(f"unknown log template: {template_id}") from exc

    @classmethod
    def from_files(
        cls,
        *,
        metrics_path: Path,
        logs_path: Path,
        allowed_services: set[str],
    ) -> QueryRegistry:
        metrics = MetricTemplateFile.model_validate(cls._read_yaml(metrics_path))
        logs = LogTemplateFile.model_validate(cls._read_yaml(logs_path))
        return cls(
            metrics=metrics.templates,
            logs=logs.templates,
            allowed_services=allowed_services,
        )

    def render_metric(self, request: MetricRenderRequest) -> str:
        if request.service not in self._allowed_services:
            raise ValueError(f"service is not registered: {request.service}")
        try:
            template = self._metrics[request.template_id]
        except KeyError as exc:
            raise ValueError(f"unknown metric template: {request.template_id}") from exc
        values = {
            "service": request.service,
            "duration": request.duration,
            "window": request.window,
            "percentile": format(request.percentile, ".6g"),
        }
        selected = {name: values[name] for name in template.parameters}
        return Template(template.expression).substitute(selected)

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"template file must contain an object: {path}")
        return cast(dict[str, Any], raw)
