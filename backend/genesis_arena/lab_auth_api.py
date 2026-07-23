"""Thin, credential-safe FastAPI surface for :mod:`genesis_arena.lab_auth`.

This module intentionally does not import the application singleton or mount a
router.  ``main.py`` can opt into it later by constructing a ``LabAuthService``
at lifespan startup, storing it on ``app.state``, registering the exception
handlers, and including the returned router before the static dashboard mount.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, NoReturn

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .lab_auth import (
    AuthenticationError,
    AuthModeError,
    CsrfValidationError,
    EmailDeliveryError,
    IssuedSession,
    LabAuthConfigurationError,
    LabAuthError,
    LabAuthService,
    MagicLinkError,
    OperatorPrincipal,
    OriginValidationError,
)

LabAuthServiceGetter = Callable[[Request], LabAuthService]
LabOperatorRequirement = Callable[[Request], Awaitable[OperatorPrincipal]]

_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
_CALLBACK_HEADERS = {
    **_NO_STORE_HEADERS,
    "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


def _auth_error_status_and_code(error: LabAuthError) -> tuple[int, str]:
    if isinstance(error, OriginValidationError):
        return 403, "lab_auth_origin_not_allowed"
    if isinstance(error, CsrfValidationError):
        return 403, "lab_auth_csrf_invalid"
    if isinstance(error, (AuthenticationError, MagicLinkError)):
        return 401, "lab_authentication_required"
    if isinstance(error, AuthModeError):
        return 404, "lab_auth_flow_unavailable"
    if isinstance(error, (EmailDeliveryError, LabAuthConfigurationError)):
        return 503, "lab_auth_unavailable"
    return 503, "lab_auth_unavailable"


def _safe_error_response(error: LabAuthError) -> JSONResponse:
    status, code = _auth_error_status_and_code(error)
    return JSONResponse(
        status_code=status,
        content={"detail": {"code": code}},
        headers=_NO_STORE_HEADERS,
    )


def _safe_http_exception(error: LabAuthError) -> HTTPException:
    status, code = _auth_error_status_and_code(error)
    return HTTPException(status_code=status, detail={"code": code}, headers=_NO_STORE_HEADERS)


def install_lab_auth_exception_handlers(app: FastAPI) -> None:
    """Install token-free error rendering for a later application integration.

    Route functions below already convert expected failures themselves.  This
    helper makes the same safe response available to future authenticated
    endpoints that call the core service directly.
    """

    async def handle_auth_error(_: Request, error: LabAuthError) -> JSONResponse:
        return _safe_error_response(error)

    for error_type in (
        AuthenticationError,
        AuthModeError,
        CsrfValidationError,
        EmailDeliveryError,
        LabAuthConfigurationError,
        MagicLinkError,
        OriginValidationError,
    ):
        app.add_exception_handler(error_type, handle_auth_error)


def create_lab_auth_router(get_service: LabAuthServiceGetter) -> APIRouter:
    """Create the isolated `/api/auth` router for one app-owned auth service.

    ``get_service`` should usually retrieve and type-check
    ``request.app.state.lab_auth``.  The factory keeps this module compatible
    with small TestClient apps and avoids any import-time application state.
    """

    if not callable(get_service):
        raise TypeError("get_service must be callable")
    router = APIRouter(prefix="/api/auth", tags=["Lab authentication"])

    def service_for(request: Request) -> LabAuthService:
        try:
            service = get_service(request)
        except Exception:
            _raise_safe_http_error(503, "lab_auth_unavailable")
        if not isinstance(service, LabAuthService):
            _raise_safe_http_error(503, "lab_auth_unavailable")
        return service

    @router.get("/configuration")
    async def authentication_configuration(request: Request) -> JSONResponse:
        """Publish only the sign-in flow selector needed by the browser shell.

        This endpoint intentionally omits the invite allowlist, database details, origin, and all
        delivery configuration. It is safe to read before an operator has a session.
        """

        service = service_for(request)
        return _json_response({"mode": service.settings.mode})

    @router.post("/magic-link", status_code=202)
    async def request_magic_link(request: Request) -> JSONResponse:
        service = service_for(request)
        payload = await _safe_json_payload(request)
        if not isinstance(payload, dict) or set(payload) != {"email"}:
            _raise_safe_http_error(422, "invalid_lab_auth_request")
        email = payload.get("email")
        if not isinstance(email, str):
            _raise_safe_http_error(422, "invalid_lab_auth_request")
        try:
            result = service.request_magic_link(
                email=email,
                origin=_origin_header(request),
            )
        except EmailDeliveryError:
            # Unknown addresses never attempt delivery; an invited address whose delivery
            # provider is unavailable must be indistinguishable at this HTTP boundary. The core
            # has already invalidated its one-use token before raising.
            return _json_response({"accepted": True}, status_code=202)
        except LabAuthError as error:
            return _safe_error_response(error)
        # The result is deliberately invariant for invited and unknown email
        # addresses.  Do not reflect either value in this response.
        return _json_response({"accepted": result.accepted}, status_code=202)

    @router.get("/callback")
    async def magic_link_callback(request: Request) -> RedirectResponse:
        service = service_for(request)
        token = request.query_params.get("token", "")
        try:
            issued = service.consume_magic_link(token)
        except LabAuthError:
            # A callback URL must never render or retain its token.  Redirect
            # to a fixed safe SPA route rather than returning an error object.
            return RedirectResponse(
                url="/?auth=invalid-link",
                status_code=303,
                headers=_CALLBACK_HEADERS,
            )
        response = RedirectResponse(url="/", status_code=303, headers=_CALLBACK_HEADERS)
        _set_session_cookie(response, issued)
        return response

    @router.get("/me")
    async def current_operator(request: Request) -> JSONResponse:
        service = service_for(request)
        try:
            principal = service.authenticate_session(_session_token(request, service))
            csrf_token = service.rotate_csrf_token(_session_token(request, service))
        except LabAuthError as error:
            return _safe_error_response(error)
        return _principal_response(principal, csrf_token.reveal())

    @router.post("/local-login")
    async def local_login(request: Request) -> JSONResponse:
        service = service_for(request)
        try:
            issued = service.start_local_session(origin=_origin_header(request))
        except LabAuthError as error:
            return _safe_error_response(error)
        response = _principal_response(issued.principal, issued.csrf_token.reveal())
        _set_session_cookie(response, issued)
        return response

    @router.post("/logout", status_code=204)
    async def logout(request: Request) -> Response:
        service = service_for(request)
        try:
            token = _session_token(request, service)
            service.authorize_unsafe_request(
                session_token=token,
                csrf_token=_csrf_header(request),
                origin=_origin_header(request),
            )
            service.logout(token)
        except LabAuthError as error:
            return _safe_error_response(error)
        response = Response(status_code=204, headers=_NO_STORE_HEADERS)
        _clear_session_cookie(response, service)
        return response

    @router.post("/csrf/rotate")
    async def rotate_csrf(request: Request) -> JSONResponse:
        service = service_for(request)
        try:
            token = _session_token(request, service)
            service.authorize_unsafe_request(
                session_token=token,
                csrf_token=_csrf_header(request),
                origin=_origin_header(request),
            )
            csrf_token = service.rotate_csrf_token(token)
        except LabAuthError as error:
            return _safe_error_response(error)
        return _json_response({"csrf_token": csrf_token.reveal()})

    return router


def create_lab_operator_requirement(get_service: LabAuthServiceGetter) -> LabOperatorRequirement:
    """Create an auth dependency for a Team Lab feature router.

    Every Lab read requires an authenticated operator. State-changing requests
    also require the session-bound CSRF nonce and exact configured Origin.
    Keeping this separate from the auth router lets the app apply it to an
    entire private API surface with FastAPI's ``dependencies`` option.
    """

    if not callable(get_service):
        raise TypeError("get_service must be callable")

    async def require_operator(request: Request) -> OperatorPrincipal:
        try:
            service = get_service(request)
        except Exception:
            _raise_safe_http_error(503, "lab_auth_unavailable")
        if not isinstance(service, LabAuthService):
            _raise_safe_http_error(503, "lab_auth_unavailable")
        try:
            token = _session_token(request, service)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                return service.authorize_unsafe_request(
                    session_token=token,
                    csrf_token=_csrf_header(request),
                    origin=_origin_header(request),
                )
            return service.authenticate_session(token)
        except LabAuthError as error:
            raise _safe_http_exception(error) from None

    return require_operator


async def _safe_json_payload(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        _raise_safe_http_error(422, "invalid_lab_auth_request")


def _origin_header(request: Request) -> str:
    # Empty/missing Origin is intentionally rejected by the core for every
    # unsafe flow; it is never reflected into a response or log message here.
    return request.headers.get("origin", "")


def _csrf_header(request: Request) -> str:
    return request.headers.get("x-worldeval-csrf", "")


def _session_token(request: Request, service: LabAuthService) -> str:
    return request.cookies.get(service.settings.session_cookie_name, "")


def _json_response(content: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=content, headers=_NO_STORE_HEADERS)


def _principal_response(principal: OperatorPrincipal, csrf_token: str) -> JSONResponse:
    """Return only the authenticated identity and an in-memory CSRF nonce."""

    return _json_response(
        {
            "operator": {
                "member_id": principal.member_id,
                "email": principal.email,
            },
            "csrf_token": csrf_token,
        }
    )


def _set_session_cookie(response: Response, issued: IssuedSession) -> None:
    cookie = issued.cookie
    response.set_cookie(
        key=cookie.name,
        value=cookie.token.reveal(),
        max_age=cookie.max_age_seconds,
        path=cookie.path,
        secure=cookie.secure,
        httponly=cookie.http_only,
        samesite=cookie.same_site,
    )


def _clear_session_cookie(response: Response, service: LabAuthService) -> None:
    response.delete_cookie(
        key=service.settings.session_cookie_name,
        path="/",
        secure=service.settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def _raise_safe_http_error(status_code: int, code: str) -> NoReturn:
    raise HTTPException(status_code=status_code, detail={"code": code}, headers=_NO_STORE_HEADERS)


__all__ = [
    "create_lab_operator_requirement",
    "create_lab_auth_router",
    "install_lab_auth_exception_handlers",
    "LabAuthServiceGetter",
    "LabOperatorRequirement",
]
