from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import os

import httpx
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine

DEFAULT_OTLP_TRACES_ENDPOINT = "http://127.0.0.1:4318/v1/traces"
DEFAULT_OTLP_METRICS_ENDPOINT = "http://127.0.0.1:4318/v1/metrics"


def create_tracer_provider(service_name: str) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
                or DEFAULT_OTLP_TRACES_ENDPOINT
            )
        )
    )
    return provider


def create_meter_provider(service_name: str) -> MeterProvider:
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
            or DEFAULT_OTLP_METRICS_ENDPOINT
        )
    )
    return MeterProvider(
        resource=Resource.create({"service.name": service_name}),
        metric_readers=[reader],
    )


def instrument_fastapi(app: FastAPI, provider: TracerProvider) -> None:
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


def instrument_httpx(client: httpx.Client | httpx.AsyncClient, provider: TracerProvider) -> None:
    HTTPXClientInstrumentor().instrument_client(client, tracer_provider=provider)


def instrument_sqlalchemy(engine: AsyncEngine, provider: TracerProvider) -> None:
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)
