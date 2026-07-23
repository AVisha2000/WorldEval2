from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from genesis_arena.lab_auth import (
    AuthenticationError,
    AuthModeError,
    CsrfValidationError,
    EmailDeliveryError,
    InMemoryEmailSender,
    LabAuthConfigurationError,
    LabAuthService,
    LabAuthSettings,
    MagicLinkError,
    OriginValidationError,
    canonical_origin,
    is_loopback_origin,
    magic_link_token_from_url,
    origin_is_allowed,
)


@dataclass
class _Clock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class _FailingSender:
    def send_magic_link(self, message) -> None:
        del message
        raise RuntimeError("provider secret must not surface")


def _clock() -> _Clock:
    return _Clock(datetime(2026, 7, 23, tzinfo=timezone.utc))


def _magic_settings(path: Path, **overrides: object) -> LabAuthSettings:
    values: dict[str, object] = {
        "mode": "magic_link",
        "public_origin": "https://lab.worldeval.example",
        "database_path": path,
        "token_pepper": b"p" * 32,
        "allowed_emails": frozenset({"operator@worldeval.example"}),
        "bind_host": "127.0.0.1",
        "environment": "production",
    }
    values.update(overrides)
    return LabAuthSettings(**values)  # type: ignore[arg-type]


def _local_settings(**overrides: object) -> LabAuthSettings:
    values: dict[str, object] = {
        "mode": "local",
        "public_origin": "http://127.0.0.1:5173",
        "database_path": Path(":memory:"),
        "token_pepper": b"l" * 32,
        "bind_host": "127.0.0.1",
        "environment": "development",
    }
    values.update(overrides)
    return LabAuthSettings(**values)  # type: ignore[arg-type]


def test_magic_link_is_invite_only_one_use_and_never_persists_raw_tokens(tmp_path: Path) -> None:
    database = tmp_path / "lab-auth.sqlite3"
    sender = InMemoryEmailSender()
    service = LabAuthService(_magic_settings(database), email_sender=sender, clock=_clock())

    accepted = service.request_magic_link(
        email="OPERATOR@WorldEval.example", origin="https://lab.worldeval.example"
    )
    unknown = service.request_magic_link(
        email="outsider@worldeval.example", origin="https://lab.worldeval.example"
    )

    assert accepted == unknown
    assert len(sender.messages) == 1
    message = sender.messages[0]
    token = magic_link_token_from_url(message.magic_link_url)
    assert token not in repr(message)
    assert token not in repr(service)
    assert token not in repr(service.settings)

    issued = service.consume_magic_link(token)
    session_token = issued.cookie.token.reveal()
    csrf_token = issued.csrf_token.reveal()
    assert issued.cookie.name == "__Host-worldeval_session"
    assert issued.cookie.secure is True
    assert issued.cookie.http_only is True
    assert issued.cookie.same_site == "lax"
    assert issued.principal.email == "operator@worldeval.example"
    assert token not in repr(issued)
    assert session_token not in repr(issued)
    assert csrf_token not in repr(issued)

    assert service.authenticate_session(session_token) == issued.principal
    assert (
        service.authorize_unsafe_request(
            session_token=session_token,
            csrf_token=csrf_token,
            origin="https://lab.worldeval.example",
        )
        == issued.principal
    )
    with pytest.raises(MagicLinkError):
        service.consume_magic_link(token)
    with pytest.raises(CsrfValidationError):
        service.authorize_unsafe_request(
            session_token=session_token,
            csrf_token="A" * 43,
            origin="https://lab.worldeval.example",
        )

    # Durable state contains HMAC digests only, never an email URL or raw cookie/CSRF token.
    service.close()
    persisted = database.read_bytes()
    for protected in (token, session_token, csrf_token, message.magic_link_url):
        assert protected.encode() not in persisted


def test_magic_link_expiry_and_delivery_failure_do_not_leave_a_usable_token(tmp_path: Path) -> None:
    clock = _clock()
    database = tmp_path / "lab-auth.sqlite3"
    sender = InMemoryEmailSender()
    settings = _magic_settings(database, magic_link_ttl_seconds=60)
    service = LabAuthService(settings, email_sender=sender, clock=clock)
    service.request_magic_link(email="operator@worldeval.example", origin=settings.public_origin)
    token = magic_link_token_from_url(sender.messages[0].magic_link_url)
    clock.advance(61)
    with pytest.raises(MagicLinkError):
        service.consume_magic_link(token)
    service.close()

    failing_database = tmp_path / "failed.sqlite3"
    failing = LabAuthService(
        _magic_settings(failing_database), email_sender=_FailingSender(), clock=_clock()
    )
    with pytest.raises(EmailDeliveryError) as error:
        failing.request_magic_link(
            email="operator@worldeval.example", origin="https://lab.worldeval.example"
        )
    assert "provider secret" not in str(error.value)
    assert error.value.__cause__ is None
    with sqlite3.connect(failing_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM magic_links").fetchone()[0] == 0


def test_local_mode_is_explicit_loopback_only_and_uses_a_distinct_insecure_cookie() -> None:
    settings = _local_settings()
    service = LabAuthService(settings, clock=_clock())

    issued = service.start_local_session(origin="http://127.0.0.1:5173")
    assert issued.cookie.name == "worldeval_local_session"
    assert issued.cookie.secure is False
    assert issued.principal.email == "local-operator@worldeval.test"
    assert (
        service.authorize_websocket(
            session_token=issued.cookie.token.reveal(), origin="http://127.0.0.1:5173"
        )
        == issued.principal
    )
    with pytest.raises(AuthModeError):
        service.request_magic_link(
            email="local-operator@worldeval.test", origin="http://127.0.0.1:5173"
        )

    with pytest.raises(LabAuthConfigurationError):
        _local_settings(public_origin="https://lab.worldeval.example")
    with pytest.raises(LabAuthConfigurationError):
        _local_settings(environment="production")
    with pytest.raises(LabAuthConfigurationError):
        _local_settings(bind_host="0.0.0.0")


def test_session_rotation_logout_and_origin_csrf_checks(tmp_path: Path) -> None:
    clock = _clock()
    sender = InMemoryEmailSender()
    settings = _magic_settings(tmp_path / "lab-auth.sqlite3")
    service = LabAuthService(settings, email_sender=sender, clock=clock)
    service.request_magic_link(email="operator@worldeval.example", origin=settings.public_origin)
    issued = service.consume_magic_link(
        magic_link_token_from_url(sender.messages[0].magic_link_url)
    )
    session_token = issued.cookie.token.reveal()
    old_csrf = issued.csrf_token.reveal()
    new_csrf = service.rotate_csrf_token(session_token).reveal()

    with pytest.raises(CsrfValidationError):
        service.authorize_unsafe_request(
            session_token=session_token, csrf_token=old_csrf, origin=settings.public_origin
        )
    with pytest.raises(OriginValidationError):
        service.authorize_unsafe_request(
            session_token=session_token, csrf_token=new_csrf, origin="https://evil.example"
        )
    assert (
        service.authorize_unsafe_request(
            session_token=session_token, csrf_token=new_csrf, origin=settings.public_origin
        )
        == issued.principal
    )
    service.logout(session_token)
    with pytest.raises(AuthenticationError):
        service.authenticate_session(session_token)


def test_origin_helpers_are_exact_and_do_not_accept_paths_or_suffixes() -> None:
    assert canonical_origin("HTTPS://Lab.WorldEval.Example:443/") == "https://lab.worldeval.example"
    assert origin_is_allowed("https://lab.worldeval.example", {"https://lab.worldeval.example"})
    assert not origin_is_allowed(
        "https://lab.worldeval.example.evil.example", {"https://lab.worldeval.example"}
    )
    assert is_loopback_origin("http://localhost:5173")
    assert is_loopback_origin("http://[::1]:5173")
    assert not is_loopback_origin("https://lab.worldeval.example")
    with pytest.raises(OriginValidationError):
        canonical_origin("https://lab.worldeval.example/a-path")
    with pytest.raises(OriginValidationError):
        canonical_origin("https://user@lab.worldeval.example")


def test_environment_configuration_fails_closed_for_production_magic_link() -> None:
    with pytest.raises(LabAuthConfigurationError):
        _magic_settings(Path(":memory:"))
    with pytest.raises(LabAuthConfigurationError):
        _magic_settings(Path("relative-auth.sqlite3"))
    with pytest.raises(LabAuthConfigurationError):
        LabAuthSettings.from_environ(
            {
                "GENESIS_AUTH_MODE": "magic_link",
                "GENESIS_AUTH_PUBLIC_ORIGIN": "https://lab.worldeval.example",
                "GENESIS_AUTH_ALLOWED_EMAILS": "operator@worldeval.example",
            }
        )
    with pytest.raises(LabAuthConfigurationError):
        LabAuthSettings.from_environ(
            {
                "GENESIS_AUTH_MODE": "magic_link",
                "GENESIS_AUTH_PUBLIC_ORIGIN": "http://lab.worldeval.example",
                "GENESIS_AUTH_DATABASE_PATH": "/var/lib/worldeval/auth.sqlite3",
                "GENESIS_AUTH_TOKEN_PEPPER": "x" * 32,
                "GENESIS_AUTH_ALLOWED_EMAILS": "operator@worldeval.example",
            }
        )

    settings = LabAuthSettings.from_environ(
        {
            "GENESIS_AUTH_MODE": "magic_link",
            "GENESIS_AUTH_PUBLIC_ORIGIN": "https://lab.worldeval.example",
            "GENESIS_AUTH_DATABASE_PATH": "/var/lib/worldeval/auth.sqlite3",
            "GENESIS_AUTH_TOKEN_PEPPER": "x" * 32,
            "GENESIS_AUTH_ALLOWED_EMAILS": "one@worldeval.example,two@worldeval.example",
        }
    )
    assert settings.allowed_emails == frozenset({"one@worldeval.example", "two@worldeval.example"})
    assert settings.secure_cookies is True
