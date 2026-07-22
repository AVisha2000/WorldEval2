from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from genesis_arena.embodiment.providers.contracts import (
    InMemoryProviderAuditLog,
    ProviderFailureKind,
    ProviderRequest,
)
from genesis_arena.embodiment.providers.openai_adapter import (
    OpenAIProviderAdapter,
    _openai_structured_output_schema,
)

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (1280).to_bytes(4, "big") + (720).to_bytes(4, "big")
ACTION_SCHEMA = {
    "type": "object",
    "properties": {"action": {"type": "string"}},
    "required": ["action"],
    "additionalProperties": False,
}


class FakeResponses:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeClient:
    def __init__(self, outcome):
        self.responses = FakeResponses(outcome)


class ClosableFakeClient(FakeClient):
    def __init__(self, outcome):
        super().__init__(outcome)
        self.closed = False

    async def close(self):
        self.closed = True


class Clock:
    def __init__(self, *values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


def request(**overrides):
    frame = overrides.get("frame_png", PNG)
    observation = {
        "episode_id": "episode-1",
        "frame": None if frame is None else {
            "height": 720,
            "mime_type": "image/png",
            "sensor_id": "operator-follow-v1",
            "sha256": hashlib.sha256(frame).hexdigest(),
            "transport_ref": "frame:player-1.4.fixture",
            "width": 1280,
        },
        "goal": "Relay ahead.",
        "observation_seq": 4,
        "profile": "text-visible-v1" if frame is None else "hybrid-visible-v1",
    }
    values = {
        "episode_id": "episode-1",
        "participant_id": "player-1",
        "observation_seq": 4,
        "deadline_monotonic_ns": 4_000_000_000,
        "model": "gpt-5.4",
        "system_prompt": "Return one valid action.",
        "observation_json": json.dumps(observation, sort_keys=True, separators=(",", ":")).encode(),
        "action_schema_json": json.dumps(ACTION_SCHEMA).encode(),
        "scratchpad_utf8": b"previous move missed",
        "frame_png": frame,
        "max_output_bytes": 1024,
    }
    values.update(overrides)
    return ProviderRequest(**values)


def response(output_text='{"action":"move"}', *, output=None):
    return SimpleNamespace(
        output_text=output_text,
        output=[] if output is None else output,
        usage=SimpleNamespace(
            input_tokens=23,
            output_tokens=7,
            input_tokens_details=SimpleNamespace(cached_tokens=3, cache_write_tokens=5),
        ),
        _request_id="req-secret-provider-id",
    )


def test_structured_output_schema_adds_explicit_const_type_without_mutating_protocol() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://worldeval.local/action.json",
        "type": "object",
        "properties": {"protocol_version": {"const": "llm-controller/0.1.0"}},
        "required": ["protocol_version"],
        "additionalProperties": False,
    }
    before = json.loads(json.dumps(schema))

    normalized = _openai_structured_output_schema(schema)

    assert normalized["properties"]["protocol_version"] == {
        "const": "llm-controller/0.1.0",
        "type": "string",
    }
    assert "$schema" not in normalized and "$id" not in normalized
    assert schema == before


@pytest.mark.asyncio
async def test_close_drops_the_credential_bearing_sdk_client_reference():
    client = ClosableFakeClient(response())
    adapter = OpenAIProviderAdapter(client=client)

    await adapter.aclose()

    assert client.closed
    assert adapter._client is None


@pytest.mark.asyncio
async def test_success_builds_private_retry_free_responses_payload_and_audit():
    client = FakeClient(response())
    audit = InMemoryProviderAuditLog()
    adapter = OpenAIProviderAdapter(
        client=client,
        audit_log=audit,
        monotonic_ns=Clock(1_000_000_000, 1_025_000_000),
    )

    result = await adapter.request(request())

    assert result.raw_output == b'{"action":"move"}'
    assert result.failure is None
    assert result.telemetry.latency_ms == 25
    assert result.telemetry.input_tokens == 23
    assert result.telemetry.output_tokens == 7
    assert result.telemetry.cached_input_tokens == 3
    assert result.telemetry.cache_write_tokens == 5
    assert result.telemetry.as_dict()["cache_write_tokens"] == 5
    assert len(result.telemetry.request_id_sha256) == 64
    assert len(client.responses.calls) == 1
    payload = client.responses.calls[0]
    assert payload["timeout"] == 3.0
    assert payload["store"] is False
    assert payload["truncation"] == "disabled"
    assert payload["text"]["format"] == {
        "type": "json_schema",
        "name": "worldarena_action",
        "strict": True,
        "schema": ACTION_SCHEMA,
    }
    content = payload["input"][0]["content"]
    assert content[0]["type"] == "input_text"
    assert "Relay ahead." in content[0]["text"]
    assert "previous move missed" in content[0]["text"]
    assert content[1]["image_url"] == (
        "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    )
    assert content[1]["detail"] == "high"
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["max_output_tokens"] == 2048
    assert "service_tier" not in payload
    serialized = repr(payload)
    assert "spectator" not in serialized
    assert "opponent" not in serialized
    assert "req-secret-provider-id" not in serialized
    records = audit.drain_episode("episode-1")
    assert len(records) == 1
    assert records[0].provider == "openai"
    assert records[0].result == result


@pytest.mark.asyncio
async def test_refusal_is_sanitized_and_recorded():
    refusal = SimpleNamespace(type="refusal", refusal="I cannot help")
    client = FakeClient(response("", output=[SimpleNamespace(content=[refusal])]))
    audit = InMemoryProviderAuditLog()
    adapter = OpenAIProviderAdapter(
        client=client, audit_log=audit, monotonic_ns=Clock(100, 200)
    )

    result = await adapter.request(request(deadline_monotonic_ns=1_000))

    assert result.raw_output is None
    assert result.failure is ProviderFailureKind.REFUSAL
    assert "I cannot help" not in repr(result)
    assert len(audit.drain_episode("episode-1")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("contains secret"), ProviderFailureKind.TIMEOUT),
        (
            type("RateLimitError", (Exception,), {"status_code": 429})("contains secret"),
            ProviderFailureKind.RATE_LIMIT,
        ),
    ],
)
async def test_provider_failures_are_sanitized_without_retry(error, expected):
    client = FakeClient(error)
    audit = InMemoryProviderAuditLog()
    adapter = OpenAIProviderAdapter(
        client=client, audit_log=audit, monotonic_ns=Clock(100, 300)
    )

    result = await adapter.request(request(deadline_monotonic_ns=1_000))

    assert result.failure is expected
    assert "secret" not in repr(result)
    assert len(client.responses.calls) == 1
    assert audit.drain_episode("episode-1")[0].result == result


@pytest.mark.asyncio
async def test_quota_429_is_distinct_from_transient_rate_limit():
    quota = type(
        "RateLimitError",
        (Exception,),
        {"status_code": 429, "body": {"error": {"code": "insufficient_quota"}}},
    )("protected provider message")
    adapter = OpenAIProviderAdapter(client=FakeClient(quota), monotonic_ns=Clock(100, 200))

    result = await adapter.request(request(deadline_monotonic_ns=1_000))

    assert result.failure is ProviderFailureKind.QUOTA


@pytest.mark.asyncio
async def test_explicit_default_service_tier_is_sent_only_when_requested():
    client = FakeClient(response())
    adapter = OpenAIProviderAdapter(
        client=client,
        service_tier="default",
        monotonic_ns=Clock(100, 200),
    )

    await adapter.request(request(deadline_monotonic_ns=1_000))

    assert client.responses.calls[0]["service_tier"] == "default"


def test_invalid_service_tier_is_rejected():
    with pytest.raises(ValueError, match="service_tier"):
        OpenAIProviderAdapter(client=FakeClient(response()), service_tier="turbo")


@pytest.mark.asyncio
async def test_expired_deadline_does_not_dispatch():
    client = FakeClient(response())
    adapter = OpenAIProviderAdapter(client=client, monotonic_ns=Clock(500, 500))

    result = await adapter.request(request(deadline_monotonic_ns=500))

    assert result.failure is ProviderFailureKind.TIMEOUT
    assert client.responses.calls == []


@pytest.mark.asyncio
async def test_oversize_utf8_output_is_rejected_without_exposing_it():
    client = FakeClient(response("four-byte-output"))
    adapter = OpenAIProviderAdapter(client=client, monotonic_ns=Clock(100, 200))

    result = await adapter.request(request(deadline_monotonic_ns=1_000, max_output_bytes=4))

    assert result.failure is ProviderFailureKind.OUTPUT_TOO_LARGE
    assert result.raw_output is None
    assert "four-byte-output" not in repr(result)


@pytest.mark.asyncio
async def test_text_only_request_omits_image_content():
    client = FakeClient(response())
    adapter = OpenAIProviderAdapter(client=client, monotonic_ns=Clock(100, 200))

    await adapter.request(request(deadline_monotonic_ns=1_000, frame_png=None))

    content = client.responses.calls[0]["input"][0]["content"]
    assert [part["type"] for part in content] == ["input_text"]
