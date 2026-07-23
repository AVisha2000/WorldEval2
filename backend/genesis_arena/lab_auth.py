"""Small, provider-neutral authentication foundation for the WorldEval Lab.

This module deliberately has no FastAPI dependency and registers no routes.  It
owns only the durable, security-sensitive parts of invite-only passwordless
authentication so a later HTTP layer can be thin and easily audited:

* invite-only membership and one-use magic-link records;
* opaque, server-side hashed session and CSRF tokens;
* an explicitly constrained local-development mode; and
* exact origin validation for cookie-authenticated unsafe requests.

Raw magic-link and session tokens exist only long enough to be sent to the
browser or email sender.  SQLite never receives them, and this module contains
no logging calls by design.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

_MAGIC_LINK_TOKEN_BYTES = 32
_SESSION_TOKEN_BYTES = 32
_CSRF_TOKEN_BYTES = 32
_MIN_PEPPER_BYTES = 32
_DEFAULT_MAGIC_LINK_TTL_SECONDS = 15 * 60
_DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
_MAX_TOKEN_LENGTH = 512


class LabAuthError(ValueError):
    """Base class for stable, token-free Lab auth failures."""


class LabAuthConfigurationError(LabAuthError):
    """Raised when authentication would start with an unsafe configuration."""


class OriginValidationError(LabAuthError):
    """Raised when a state-changing request is not from a fixed allowed origin."""


class AuthenticationError(LabAuthError):
    """Raised for an expired, revoked, or otherwise unknown session."""


class CsrfValidationError(LabAuthError):
    """Raised when an authenticated unsafe request lacks a valid CSRF token."""


class MagicLinkError(LabAuthError):
    """Raised for an expired, consumed, or otherwise invalid magic link."""


class AuthModeError(LabAuthError):
    """Raised when a caller invokes a flow unavailable in the configured mode."""


class EmailDeliveryError(LabAuthError):
    """Raised without provider detail when magic-link delivery does not complete."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("clock must return a datetime")
    if value.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> int:
    return int(_as_utc(value).timestamp())


def normalize_email(value: object) -> str:
    """Return a deliberately conservative canonical form suitable for an allowlist.

    Email syntax is not an authorization mechanism.  This only rejects clearly
    malformed values and case-folds the address; it intentionally does not apply
    provider-specific transformations such as Gmail dot or plus stripping.
    """

    if not isinstance(value, str):
        raise LabAuthError("email address is invalid")
    email = value.strip().casefold()
    if not email or len(email) > 320 or any(character.isspace() for character in email):
        raise LabAuthError("email address is invalid")
    local, separator, domain = email.rpartition("@")
    if not separator or not local or not domain or "." not in domain:
        raise LabAuthError("email address is invalid")
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        raise LabAuthError("email address is invalid")
    return email


def canonical_origin(value: object) -> str:
    """Canonicalize one web origin and reject paths, credentials, and wildcards."""

    if not isinstance(value, str) or not value:
        raise OriginValidationError("request origin is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise OriginValidationError("request origin is invalid") from exc
    scheme = parsed.scheme.casefold()
    host = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise OriginValidationError("request origin is invalid")
    normalized_host = host.rstrip(".").casefold()
    if not normalized_host:
        raise OriginValidationError("request origin is invalid")
    host_part = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    if port is None or (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return f"{scheme}://{host_part}"
    return f"{scheme}://{host_part}:{port}"


def is_loopback_host(value: object) -> bool:
    """Return true only for literal loopback addresses or localhost.

    Avoid DNS resolution here: a hostname that happens to resolve locally today
    must not make a production deployment eligible for local authentication.
    """

    if not isinstance(value, str):
        return False
    host = value.rstrip(".").casefold()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_origin(value: object) -> bool:
    try:
        parsed = urlsplit(canonical_origin(value))
    except OriginValidationError:
        return False
    return is_loopback_host(parsed.hostname)


def origin_is_allowed(origin: object, allowed_origins: Iterable[str]) -> bool:
    """Compare exact canonical origins; no wildcard or suffix matching is allowed."""

    try:
        candidate = canonical_origin(origin)
    except OriginValidationError:
        return False
    return any(hmac.compare_digest(candidate, allowed) for allowed in allowed_origins)


@dataclass(frozen=True)
class LabAuthSettings:
    """Validated, server-only Lab authentication configuration.

    ``token_pepper`` is intentionally excluded from repr.  It is not a user
    credential, but it is still secret material used to make durable token
    digests non-reversible if the SQLite file is copied.
    """

    mode: str
    public_origin: str
    database_path: Path
    token_pepper: bytes = field(repr=False)
    allowed_emails: frozenset[str] = field(default_factory=frozenset, repr=False)
    bind_host: str = "127.0.0.1"
    environment: str = "development"
    local_email: str = "local-operator@worldeval.test"
    session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS
    magic_link_ttl_seconds: int = _DEFAULT_MAGIC_LINK_TTL_SECONDS
    extra_allowed_origins: frozenset[str] = field(default_factory=frozenset, repr=False)

    def __post_init__(self) -> None:
        mode = self.mode.casefold() if isinstance(self.mode, str) else ""
        if mode not in {"local", "magic_link"}:
            raise LabAuthConfigurationError("authentication mode is invalid")
        object.__setattr__(self, "mode", mode)
        if not isinstance(self.database_path, Path):
            raise LabAuthConfigurationError("authentication database path is invalid")
        try:
            origin = canonical_origin(self.public_origin)
        except OriginValidationError as exc:
            raise LabAuthConfigurationError("authentication public origin is invalid") from exc
        object.__setattr__(self, "public_origin", origin)
        environment = self.environment.casefold() if isinstance(self.environment, str) else ""
        if environment not in {"development", "test", "production"}:
            raise LabAuthConfigurationError("authentication environment is invalid")
        object.__setattr__(self, "environment", environment)
        if not isinstance(self.bind_host, str) or not self.bind_host:
            raise LabAuthConfigurationError("authentication bind host is invalid")
        if not isinstance(self.token_pepper, bytes) or len(self.token_pepper) < _MIN_PEPPER_BYTES:
            raise LabAuthConfigurationError("authentication token pepper is invalid")
        if (
            isinstance(self.session_ttl_seconds, bool)
            or not isinstance(self.session_ttl_seconds, int)
            or not 60 <= self.session_ttl_seconds <= 7 * 24 * 60 * 60
        ):
            raise LabAuthConfigurationError("authentication session lifetime is invalid")
        if (
            isinstance(self.magic_link_ttl_seconds, bool)
            or not isinstance(self.magic_link_ttl_seconds, int)
            or not 60 <= self.magic_link_ttl_seconds <= 60 * 60
        ):
            raise LabAuthConfigurationError("magic-link lifetime is invalid")
        try:
            normalized_allowlist = frozenset(
                normalize_email(email) for email in self.allowed_emails
            )
            normalized_local_email = normalize_email(self.local_email)
            extra_origins = frozenset(canonical_origin(item) for item in self.extra_allowed_origins)
        except (LabAuthError, OriginValidationError) as exc:
            raise LabAuthConfigurationError(
                "authentication allowlist or origin is invalid"
            ) from exc
        object.__setattr__(self, "allowed_emails", normalized_allowlist)
        object.__setattr__(self, "local_email", normalized_local_email)
        object.__setattr__(self, "extra_allowed_origins", extra_origins)
        if mode == "magic_link":
            if origin.startswith("http://"):
                raise LabAuthConfigurationError("magic-link authentication requires HTTPS")
            if not normalized_allowlist:
                raise LabAuthConfigurationError(
                    "magic-link authentication requires an invite allowlist"
                )
            if str(self.database_path) == ":memory:":
                raise LabAuthConfigurationError(
                    "magic-link authentication requires persistent database state"
                )
            if not self.database_path.is_absolute():
                raise LabAuthConfigurationError(
                    "magic-link authentication requires an absolute database path"
                )
            if extra_origins:
                raise LabAuthConfigurationError(
                    "production authentication accepts one fixed origin"
                )
        else:
            if environment not in {"development", "test"}:
                raise LabAuthConfigurationError("local authentication is development-only")
            if not is_loopback_host(self.bind_host) or not is_loopback_origin(origin):
                raise LabAuthConfigurationError(
                    "local authentication requires loopback-only hosting"
                )
            if any(not is_loopback_origin(item) for item in extra_origins):
                raise LabAuthConfigurationError(
                    "local authentication accepts loopback origins only"
                )

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset((self.public_origin, *self.extra_allowed_origins))

    @property
    def secure_cookies(self) -> bool:
        return self.mode == "magic_link"

    @property
    def session_cookie_name(self) -> str:
        # The __Host- prefix requires Secure.  Local HTTP needs a distinct,
        # deliberately non-production cookie name instead of weakening it.
        return "__Host-worldeval_session" if self.secure_cookies else "worldeval_local_session"

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> LabAuthSettings:
        """Read only explicit ``GENESIS_AUTH_*`` server configuration.

        Magic-link mode fails closed without a configured pepper, HTTPS public
        origin, persistent database path, and allowlist.  Local mode deliberately
        defaults to loopback-only ephemeral state for a fresh local checkout.
        """

        source = os.environ if environ is None else environ
        mode = source.get("GENESIS_AUTH_MODE", "local")
        environment = source.get("GENESIS_AUTH_ENVIRONMENT", "development")
        bind_host = source.get("GENESIS_HOST", "127.0.0.1")
        public_origin = source.get("GENESIS_AUTH_PUBLIC_ORIGIN", "http://127.0.0.1:5173")
        database_path = Path(source.get("GENESIS_AUTH_DATABASE_PATH", ":memory:"))
        raw_allowlist = source.get("GENESIS_AUTH_ALLOWED_EMAILS", "")
        allowed_emails = frozenset(
            item.strip() for item in raw_allowlist.split(",") if item.strip()
        )
        pepper_value = source.get("GENESIS_AUTH_TOKEN_PEPPER")
        if pepper_value is None:
            if mode.casefold() == "magic_link":
                raise LabAuthConfigurationError("authentication token pepper is required")
            token_pepper = secrets.token_bytes(_MIN_PEPPER_BYTES)
        else:
            token_pepper = pepper_value.encode("utf-8")
        raw_extra_origins = source.get("GENESIS_AUTH_EXTRA_ALLOWED_ORIGINS", "")
        extra_allowed_origins = frozenset(
            item.strip() for item in raw_extra_origins.split(",") if item.strip()
        )
        return cls(
            mode=mode,
            public_origin=public_origin,
            database_path=database_path,
            token_pepper=token_pepper,
            allowed_emails=allowed_emails,
            bind_host=bind_host,
            environment=environment,
            local_email=source.get("GENESIS_AUTH_LOCAL_EMAIL", "local-operator@worldeval.test"),
            session_ttl_seconds=_parse_int(
                source.get("GENESIS_AUTH_SESSION_TTL_SECONDS"), _DEFAULT_SESSION_TTL_SECONDS
            ),
            magic_link_ttl_seconds=_parse_int(
                source.get("GENESIS_AUTH_MAGIC_LINK_TTL_SECONDS"), _DEFAULT_MAGIC_LINK_TTL_SECONDS
            ),
            extra_allowed_origins=extra_allowed_origins,
        )


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise LabAuthConfigurationError("authentication integer setting is invalid") from exc


class OpaqueToken:
    """Token wrapper that prevents accidental ``str()``/repr disclosure."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not _is_token_shape(value):
            raise ValueError("opaque token is invalid")
        self._value = value

    @classmethod
    def create(cls, bytes_length: int) -> OpaqueToken:
        return cls(secrets.token_urlsafe(bytes_length))

    def reveal(self) -> str:
        """Return the token only to the HTTP cookie or email delivery boundary."""

        return self._value

    def __repr__(self) -> str:
        return "OpaqueToken(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


def _is_token_shape(value: object) -> bool:
    if not isinstance(value, str) or not 20 <= len(value) <= _MAX_TOKEN_LENGTH:
        return False
    return value.isascii() and all(character.isalnum() or character in "-_" for character in value)


@dataclass(frozen=True)
class OperatorPrincipal:
    member_id: int
    email: str = field(repr=False)

    def __repr__(self) -> str:
        return f"OperatorPrincipal(member_id={self.member_id})"


@dataclass(frozen=True)
class SessionCookie:
    name: str
    token: OpaqueToken = field(repr=False)
    max_age_seconds: int
    secure: bool
    http_only: bool = True
    same_site: str = "lax"
    path: str = "/"

    def __repr__(self) -> str:
        return (
            "SessionCookie("
            f"name={self.name!r}, token=<redacted>, max_age_seconds={self.max_age_seconds}, "
            f"secure={self.secure}, http_only={self.http_only}, same_site={self.same_site!r}, "
            f"path={self.path!r})"
        )


@dataclass(frozen=True)
class IssuedSession:
    principal: OperatorPrincipal
    cookie: SessionCookie
    csrf_token: OpaqueToken = field(repr=False)

    def __repr__(self) -> str:
        return (
            "IssuedSession("
            f"principal={self.principal!r}, cookie={self.cookie!r}, csrf_token=<redacted>)"
        )


@dataclass(frozen=True)
class MagicLinkRequestResult:
    """Generic result deliberately identical for invited and unknown addresses."""

    accepted: bool = True


@dataclass(frozen=True)
class MagicLinkEmail:
    """Email payload passed to an adapter; repr intentionally hides its token-bearing URL."""

    recipient: str = field(repr=False)
    magic_link_url: str = field(repr=False)
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            "MagicLinkEmail("
            f"recipient=<redacted>, magic_link_url=<redacted>, expires_at={self.expires_at!r})"
        )


class EmailSender(Protocol):
    """Boundary for a future transactional-email implementation."""

    def send_magic_link(self, message: MagicLinkEmail) -> None:
        """Deliver one magic-link email without logging the URL."""


class InMemoryEmailSender:
    """Test-only sender; keep it out of production construction paths."""

    def __init__(self) -> None:
        self._messages: list[MagicLinkEmail] = []

    def send_magic_link(self, message: MagicLinkEmail) -> None:
        if not isinstance(message, MagicLinkEmail):
            raise TypeError("magic-link message is invalid")
        self._messages.append(message)

    @property
    def messages(self) -> tuple[MagicLinkEmail, ...]:
        return tuple(self._messages)


class LabAuthService:
    """Thread-safe durable auth state with no web-framework or provider coupling."""

    def __init__(
        self,
        settings: LabAuthSettings,
        *,
        email_sender: EmailSender | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(settings, LabAuthSettings):
            raise TypeError("settings must be LabAuthSettings")
        if settings.mode == "magic_link" and email_sender is None:
            raise LabAuthConfigurationError("magic-link authentication requires an email sender")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._settings = settings
        self._email_sender = email_sender
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False
        self._connection = self._open_database(settings.database_path)
        self._initialize_schema()
        self._seed_allowlist()

    def __repr__(self) -> str:
        return f"LabAuthService(mode={self._settings.mode!r}, closed={self._closed})"

    @property
    def settings(self) -> LabAuthSettings:
        return self._settings

    @staticmethod
    def _open_database(path: Path) -> sqlite3.Connection:
        target = str(path)
        if target != ":memory:":
            directory = path.parent
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        return connection

    def _initialize_schema(self) -> None:
        # sqlite3.executescript() manages its own transaction boundary, so it
        # must not be nested inside _transaction().
        with self._lock:
            self._assert_open()
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS members (
                    member_id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL CHECK(active IN (0, 1)),
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS magic_links (
                    token_digest BLOB PRIMARY KEY,
                    member_id INTEGER NOT NULL REFERENCES members(member_id),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS magic_links_expiry_idx
                    ON magic_links(expires_at);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_digest BLOB PRIMARY KEY,
                    csrf_digest BLOB NOT NULL,
                    member_id INTEGER NOT NULL REFERENCES members(member_id),
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry_idx
                    ON sessions(expires_at);
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._assert_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def _seed_allowlist(self) -> None:
        if not self._settings.allowed_emails:
            return
        now = _timestamp(self._clock())
        with self._transaction() as connection:
            for email in self._settings.allowed_emails:
                connection.execute(
                    """
                    INSERT INTO members(email, active, created_at)
                    VALUES (?, 1, ?)
                    ON CONFLICT(email) DO UPDATE SET active = 1
                    """,
                    (email, now),
                )

    def request_magic_link(self, *, email: str, origin: str) -> MagicLinkRequestResult:
        """Create and send a one-use link for an invited operator only.

        Unknown and inactive addresses return the same generic accepted result
        without a delivery attempt.  A route should expose this result unchanged.
        """

        self.require_allowed_origin(origin)
        if self._settings.mode != "magic_link":
            raise AuthModeError("magic-link authentication is unavailable")
        normalized_email = normalize_email(email)
        if normalized_email not in self._settings.allowed_emails:
            return MagicLinkRequestResult()
        now = _timestamp(self._clock())
        with self._transaction() as connection:
            member = connection.execute(
                "SELECT member_id FROM members WHERE email = ? AND active = 1", (normalized_email,)
            ).fetchone()
            if member is None:
                return MagicLinkRequestResult()
            token = OpaqueToken.create(_MAGIC_LINK_TOKEN_BYTES)
            digest = self._digest("magic-link", token.reveal())
            expires_at = now + self._settings.magic_link_ttl_seconds
            connection.execute(
                """
                INSERT INTO magic_links(
                    token_digest, member_id, created_at, expires_at, consumed_at
                )
                VALUES (?, ?, ?, ?, NULL)
                """,
                (digest, int(member["member_id"]), now, expires_at),
            )
        message = MagicLinkEmail(
            recipient=normalized_email,
            magic_link_url=self._magic_link_url(token),
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        )
        try:
            assert self._email_sender is not None
            self._email_sender.send_magic_link(message)
        except Exception:
            # Never leave a deliverable token valid if its delivery did not finish.
            with self._transaction() as connection:
                connection.execute("DELETE FROM magic_links WHERE token_digest = ?", (digest,))
            raise EmailDeliveryError("magic-link delivery is unavailable") from None
        return MagicLinkRequestResult()

    def consume_magic_link(self, token: str) -> IssuedSession:
        """Consume a single valid email token and mint a fresh cookie session."""

        if self._settings.mode != "magic_link":
            raise AuthModeError("magic-link authentication is unavailable")
        digest = self._digest("magic-link", _validated_raw_token(token))
        now = _timestamp(self._clock())
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT magic_links.member_id, members.email, magic_links.expires_at,
                       magic_links.consumed_at,
                       members.active
                FROM magic_links
                JOIN members ON members.member_id = magic_links.member_id
                WHERE magic_links.token_digest = ?
                """,
                (digest,),
            ).fetchone()
            if (
                row is None
                or int(row["active"]) != 1
                or not self._member_is_allowed(str(row["email"]))
                or row["consumed_at"] is not None
                or int(row["expires_at"]) < now
            ):
                raise MagicLinkError("magic link is invalid or expired")
            changed = connection.execute(
                """
                UPDATE magic_links SET consumed_at = ?
                WHERE token_digest = ? AND consumed_at IS NULL AND expires_at >= ?
                """,
                (now, digest, now),
            ).rowcount
            if changed != 1:
                raise MagicLinkError("magic link is invalid or expired")
            return self._issue_session(
                connection,
                member_id=int(row["member_id"]),
                email=str(row["email"]),
                now=now,
            )

    def start_local_session(self, *, origin: str) -> IssuedSession:
        """Start the explicit loopback-only development identity flow."""

        self.require_allowed_origin(origin)
        if self._settings.mode != "local":
            raise AuthModeError("local authentication is unavailable")
        now = _timestamp(self._clock())
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO members(email, active, created_at)
                VALUES (?, 1, ?)
                ON CONFLICT(email) DO UPDATE SET active = 1
                """,
                (self._settings.local_email, now),
            )
            member = connection.execute(
                "SELECT member_id FROM members WHERE email = ? AND active = 1",
                (self._settings.local_email,),
            ).fetchone()
            assert member is not None
            return self._issue_session(
                connection,
                member_id=int(member["member_id"]),
                email=self._settings.local_email,
                now=now,
            )

    def authenticate_session(self, session_token: str) -> OperatorPrincipal:
        digest = self._digest("session", _validated_raw_token(session_token))
        now = _timestamp(self._clock())
        with self._transaction() as connection:
            row = self._session_row(connection, digest)
            if row is None or not self._session_is_active(row, now):
                self._revoke_if_present(connection, digest, now)
                raise AuthenticationError("session is invalid or expired")
            return OperatorPrincipal(member_id=int(row["member_id"]), email=str(row["email"]))

    def rotate_csrf_token(self, session_token: str) -> OpaqueToken:
        digest = self._digest("session", _validated_raw_token(session_token))
        now = _timestamp(self._clock())
        with self._transaction() as connection:
            row = self._session_row(connection, digest)
            if row is None or not self._session_is_active(row, now):
                self._revoke_if_present(connection, digest, now)
                raise AuthenticationError("session is invalid or expired")
            csrf = OpaqueToken.create(_CSRF_TOKEN_BYTES)
            connection.execute(
                "UPDATE sessions SET csrf_digest = ? WHERE token_digest = ?",
                (self._digest("csrf", csrf.reveal()), digest),
            )
            return csrf

    def authorize_unsafe_request(
        self, *, session_token: str, csrf_token: str, origin: str
    ) -> OperatorPrincipal:
        """Validate exact origin, active session, and the session-bound CSRF nonce."""

        self.require_allowed_origin(origin)
        session_digest = self._digest("session", _validated_raw_token(session_token))
        csrf_digest = self._digest("csrf", _validated_raw_token(csrf_token))
        now = _timestamp(self._clock())
        with self._transaction() as connection:
            row = self._session_row(connection, session_digest)
            if row is None or not self._session_is_active(row, now):
                self._revoke_if_present(connection, session_digest, now)
                raise AuthenticationError("session is invalid or expired")
            if not hmac.compare_digest(bytes(row["csrf_digest"]), csrf_digest):
                raise CsrfValidationError("CSRF token is invalid")
            return OperatorPrincipal(member_id=int(row["member_id"]), email=str(row["email"]))

    def authorize_websocket(self, *, session_token: str, origin: str) -> OperatorPrincipal:
        """Authorize browser WebSockets, which cannot supply a custom CSRF header."""

        self.require_allowed_origin(origin)
        return self.authenticate_session(session_token)

    def logout(self, session_token: str) -> None:
        """Revoke a session without revealing whether it was present."""

        digest = self._digest("session", _validated_raw_token(session_token))
        now = _timestamp(self._clock())
        with self._transaction() as connection:
            self._revoke_if_present(connection, digest, now)

    def require_allowed_origin(self, origin: str) -> None:
        if not origin_is_allowed(origin, self._settings.allowed_origins):
            raise OriginValidationError("request origin is not allowed")

    def purge_expired(self) -> None:
        """Remove expired records without returning or materializing their secrets."""

        now = _timestamp(self._clock())
        with self._transaction() as connection:
            connection.execute("DELETE FROM magic_links WHERE expires_at < ?", (now,))
            connection.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _session_row(self, connection: sqlite3.Connection, digest: bytes) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT sessions.member_id, sessions.csrf_digest, sessions.expires_at,
                   sessions.revoked_at,
                   members.email, members.active
            FROM sessions
            JOIN members ON members.member_id = sessions.member_id
            WHERE sessions.token_digest = ?
            """,
            (digest,),
        ).fetchone()

    def _session_is_active(self, row: sqlite3.Row, now: int) -> bool:
        return (
            int(row["active"]) == 1
            and self._member_is_allowed(str(row["email"]))
            and row["revoked_at"] is None
            and int(row["expires_at"]) >= now
        )

    def _member_is_allowed(self, email: str) -> bool:
        return self._settings.mode == "local" or email in self._settings.allowed_emails

    @staticmethod
    def _revoke_if_present(connection: sqlite3.Connection, digest: bytes, now: int) -> None:
        connection.execute(
            "UPDATE sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE token_digest = ?",
            (now, digest),
        )

    def _issue_session(
        self, connection: sqlite3.Connection, *, member_id: int, email: str, now: int
    ) -> IssuedSession:
        session_token = OpaqueToken.create(_SESSION_TOKEN_BYTES)
        csrf_token = OpaqueToken.create(_CSRF_TOKEN_BYTES)
        connection.execute(
            """
            INSERT INTO sessions(
                token_digest, csrf_digest, member_id, issued_at, expires_at, revoked_at
            )
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (
                self._digest("session", session_token.reveal()),
                self._digest("csrf", csrf_token.reveal()),
                member_id,
                now,
                now + self._settings.session_ttl_seconds,
            ),
        )
        principal = OperatorPrincipal(member_id=member_id, email=email)
        return IssuedSession(
            principal=principal,
            cookie=SessionCookie(
                name=self._settings.session_cookie_name,
                token=session_token,
                max_age_seconds=self._settings.session_ttl_seconds,
                secure=self._settings.secure_cookies,
            ),
            csrf_token=csrf_token,
        )

    def _magic_link_url(self, token: OpaqueToken) -> str:
        query = urlencode({"token": token.reveal()})
        return f"{self._settings.public_origin}/api/auth/callback?{query}"

    def _digest(self, purpose: str, raw_token: str) -> bytes:
        material = purpose.encode("ascii") + b"\x00" + raw_token.encode("ascii")
        return hmac.new(self._settings.token_pepper, material, hashlib.sha256).digest()

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("Lab auth service is closed")


def magic_link_token_from_url(value: str) -> str:
    """Small test/helper parser that validates a token-bearing callback URL."""

    try:
        parsed = urlsplit(value)
        values = parse_qs(parsed.query, strict_parsing=True)
    except ValueError as exc:
        raise MagicLinkError("magic link is invalid or expired") from exc
    tokens = values.get("token")
    if tokens is None or len(tokens) != 1:
        raise MagicLinkError("magic link is invalid or expired")
    return _validated_raw_token(tokens[0])


def _validated_raw_token(value: object) -> str:
    if not _is_token_shape(value):
        raise AuthenticationError("authentication token is invalid")
    assert isinstance(value, str)
    return value


__all__ = [
    "AuthenticationError",
    "AuthModeError",
    "canonical_origin",
    "CsrfValidationError",
    "EmailDeliveryError",
    "EmailSender",
    "InMemoryEmailSender",
    "IssuedSession",
    "is_loopback_host",
    "is_loopback_origin",
    "LabAuthConfigurationError",
    "LabAuthError",
    "LabAuthService",
    "LabAuthSettings",
    "MagicLinkEmail",
    "MagicLinkError",
    "MagicLinkRequestResult",
    "magic_link_token_from_url",
    "normalize_email",
    "OpaqueToken",
    "OperatorPrincipal",
    "origin_is_allowed",
    "OriginValidationError",
    "SessionCookie",
]
