"""Retry-free OpenAI Responses adapter for player-scoped embodiment requests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Any, Callable

from .contracts import (
    InMemoryProviderAuditLog,
    ProviderAuditRecord,
    ProviderCallResult,
    ProviderFailureKind,
    ProviderRequest,
    ProviderTelemetry,
)

logger = logging.getLogger(__name__)


class OpenAIProviderAdapter:
    """Translate the provider-neutral contract into one OpenAI Responses call.

    A caller may inject an SDK-compatible client for tests.  Production callers can instead pass
    an API key; the resulting SDK client is explicitly configured with retries disabled.  The key
    is never retained by this adapter after client construction and is never represented in audit
    evidence.
    """

    provider_name = "openai"

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        audit_log: InMemoryProviderAuditLog | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        service_tier: str | None = None,
    ) -> None:
        if client is not None and api_key is not None:
            raise ValueError("pass either client or api_key, not both")
        if client is None:
            if not isinstance(api_key, str) or not api_key:
                raise ValueError("api_key is required when client is not injected")
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, max_retries=0)
        if service_tier not in {None, "default", "flex", "priority"}:
            raise ValueError("service_tier is invalid")
        self._client = client
        self._audit_log = audit_log or InMemoryProviderAuditLog()
        self._monotonic_ns = monotonic_ns
        self._service_tier = service_tier

    @property
    def audit_log(self) -> InMemoryProviderAuditLog:
        """Return the process-local protected evidence sink."""

        return self._audit_log

    async def request(self, request: ProviderRequest) -> ProviderCallResult:
        if not isinstance(request, ProviderRequest):
            raise TypeError("request must be ProviderRequest")

        started_ns = self._monotonic_ns()
        remaining_ns = request.deadline_monotonic_ns - started_ns
        if remaining_ns <= 0:
            return self._finish(
                request,
                started_ns,
                ProviderFailureKind.TIMEOUT,
            )

        try:
            payload = _build_payload(request)
            if self._service_tier is not None:
                payload["service_tier"] = self._service_tier
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return self._finish(
                request,
                started_ns,
                ProviderFailureKind.INVALID_RESPONSE,
            )

        try:
            # The per-call timeout is exactly the decision-window budget remaining at dispatch.
            response = await asyncio.wait_for(
                self._client.responses.create(
                    **payload,
                    timeout=remaining_ns / 1_000_000_000,
                ),
                timeout=remaining_ns / 1_000_000_000,
            )
            completed_ns = self._monotonic_ns()
            telemetry = _telemetry(response, started_ns, completed_ns)

            if _field(response, "status", "completed") != "completed":
                result = ProviderCallResult.failed(ProviderFailureKind.INVALID_RESPONSE, telemetry)
            elif _has_refusal(response):
                result = ProviderCallResult.failed(ProviderFailureKind.REFUSAL, telemetry)
            else:
                output_text = _field(response, "output_text")
                if not isinstance(output_text, str) or not output_text:
                    result = ProviderCallResult.failed(
                        ProviderFailureKind.INVALID_RESPONSE, telemetry
                    )
                else:
                    raw_output = output_text.encode("utf-8")
                    if len(raw_output) > request.max_output_bytes:
                        result = ProviderCallResult.failed(
                            ProviderFailureKind.OUTPUT_TOO_LARGE, telemetry
                        )
                    else:
                        result = ProviderCallResult.success(raw_output, telemetry)
            self._record(request, result, started_ns, completed_ns)
            return result
        except Exception as error:  # SDK failures are deliberately collapsed to safe enums.
            # Keep this intentionally limited to provider metadata: request content, output, and
            # credentials are protected episode material and must never reach application logs.
            logger.warning(
                "OpenAI Responses request failed: type=%s status=%s code=%s parameter=%s",
                type(error).__name__,
                getattr(error, "status_code", None),
                getattr(error, "code", None),
                getattr(error, "param", None),
            )
            return self._finish(request, started_ns, _classify_failure(error))

    def _finish(
        self,
        request: ProviderRequest,
        started_ns: int,
        failure: ProviderFailureKind,
    ) -> ProviderCallResult:
        completed_ns = self._monotonic_ns()
        result = ProviderCallResult.failed(
            failure,
            ProviderTelemetry(latency_ms=max(0, completed_ns - started_ns) // 1_000_000),
        )
        self._record(request, result, started_ns, completed_ns)
        return result

    def _record(
        self,
        request: ProviderRequest,
        result: ProviderCallResult,
        started_ns: int,
        completed_ns: int,
    ) -> None:
        self._audit_log.record(
            ProviderAuditRecord(
                provider=self.provider_name,
                request=request,
                result=result,
                started_monotonic_ns=started_ns,
                completed_monotonic_ns=completed_ns,
            )
        )

    async def aclose(self) -> None:
        """Close the SDK transport and drop its retained credential-bearing client."""

        client = self._client
        try:
            close = getattr(client, "close", None)
            if callable(close):
                value = close()
                if hasattr(value, "__await__"):
                    await value
        finally:
            self._client = None


def _build_payload(request: ProviderRequest) -> dict[str, Any]:
    observation = request.observation_json.decode("utf-8", errors="strict")
    schema = json.loads(request.action_schema_json.decode("utf-8", errors="strict"))
    if not isinstance(schema, dict):
        raise ValueError("action schema must be a JSON object")

    text = f"Player-visible observation JSON:\n{observation}"
    if request.scratchpad_utf8:
        scratchpad = request.scratchpad_utf8.decode("utf-8", errors="strict")
        text += f"\n\nEpisode scratchpad:\n{scratchpad}"

    content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    if request.frame_png is not None:
        encoded = base64.b64encode(request.frame_png).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{encoded}",
                "detail": "high",
            }
        )

    return {
        "model": request.model,
        "instructions": request.system_prompt,
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "worldarena_action",
                "strict": True,
                "schema": _openai_structured_output_schema(schema),
            }
        },
        "store": False,
        "truncation": "disabled",
        "max_output_tokens": 2048,
        "reasoning": {"effort": "low"},
    }


def _openai_structured_output_schema(value: Any) -> Any:
    """Return a provider-compatible copy without changing the frozen protocol schema.

    OpenAI Structured Outputs requires an explicit ``type`` beside ``const`` even though JSON
    Schema 2020-12 can infer the instance type from the constant. Provider output is still checked
    against the unchanged repository schema at the boundary after generation.
    """

    if isinstance(value, list):
        return [_openai_structured_output_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {
        key: _openai_structured_output_schema(item)
        for key, item in value.items()
        if key not in {"$schema", "$id"}
    }
    if "const" in normalized and "type" not in normalized:
        constant = normalized["const"]
        if isinstance(constant, bool):
            normalized["type"] = "boolean"
        elif isinstance(constant, int):
            normalized["type"] = "integer"
        elif isinstance(constant, float):
            normalized["type"] = "number"
        elif isinstance(constant, str):
            normalized["type"] = "string"
        elif constant is None:
            normalized["type"] = "null"
    return normalized


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _has_refusal(response: Any) -> bool:
    for item in _field(response, "output", ()) or ():
        for content in _field(item, "content", ()) or ():
            if _field(content, "type") == "refusal" or _field(content, "refusal"):
                return True
    return False


def _telemetry(response: Any, started_ns: int, completed_ns: int) -> ProviderTelemetry:
    usage = _field(response, "usage")
    input_details = _field(usage, "input_tokens_details") if usage is not None else None
    request_id = _field(response, "_request_id")
    request_id_sha256 = None
    if isinstance(request_id, str) and request_id:
        request_id_sha256 = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return ProviderTelemetry(
        latency_ms=max(0, completed_ns - started_ns) // 1_000_000,
        input_tokens=_non_negative_int(_field(usage, "input_tokens")),
        output_tokens=_non_negative_int(_field(usage, "output_tokens")),
        cached_input_tokens=_non_negative_int(_field(input_details, "cached_tokens")),
        cache_write_tokens=_non_negative_int(_field(input_details, "cache_write_tokens")),
        request_id_sha256=request_id_sha256,
    )


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _classify_failure(error: Exception) -> ProviderFailureKind:
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return ProviderFailureKind.TIMEOUT

    name = type(error).__name__.lower()
    status_code = getattr(error, "status_code", None)
    error_codes = _safe_error_codes(error)
    if "timeout" in name:
        return ProviderFailureKind.TIMEOUT
    if error_codes & {"insufficient_quota", "billing_hard_limit_reached", "quota_exceeded"}:
        return ProviderFailureKind.QUOTA
    if status_code == 429 or "ratelimit" in name or "rate_limit" in name:
        return ProviderFailureKind.RATE_LIMIT
    if status_code in (401, 403) or "authentication" in name or "permission" in name:
        return ProviderFailureKind.CREDENTIAL
    if "connection" in name or "transport" in name:
        return ProviderFailureKind.TRANSPORT
    if isinstance(status_code, int):
        return ProviderFailureKind.INTERNAL
    return ProviderFailureKind.INTERNAL


def _safe_error_codes(error: Exception) -> set[str]:
    """Read only stable SDK code/type fields; never retain messages or response bodies."""

    values = []
    for name in ("code", "type"):
        values.append(getattr(error, name, None))
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        values.extend((body.get("code"), body.get("type")))
        nested = body.get("error")
        if isinstance(nested, dict):
            values.extend((nested.get("code"), nested.get("type")))
    return {value.casefold() for value in values if isinstance(value, str) and value}
