from __future__ import annotations

import hashlib

from incidentpilot.observability.attributes import SpanAttributes, set_safe_attributes


def record_genai_call(
    span: SpanAttributes,
    *,
    workflow_name: str,
    model: str,
    prompt: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record GenAI operational metadata without retaining prompt content."""
    set_safe_attributes(
        span,
        {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.request.model": model,
            "gen_ai.input.messages.digest": hashlib.sha256(prompt.encode()).hexdigest(),
            "gen_ai.input.messages.length": len(prompt),
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "incidentpilot.workflow.name": workflow_name,
        },
    )
