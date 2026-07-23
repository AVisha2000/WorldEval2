from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from genesis_arena.embodiment.api import router as embodiment_router
from genesis_arena.embodiment.lab_api import router as lab_router
from genesis_arena.lab_auth import (
    InMemoryEmailSender,
    LabAuthService,
    LabAuthSettings,
    magic_link_token_from_url,
)
from genesis_arena.lab_auth_api import (
    create_lab_auth_router,
    create_lab_operator_requirement,
    install_lab_auth_exception_handlers,
)


def _local_service() -> LabAuthService:
    return LabAuthService(
        LabAuthSettings(
            mode="local",
            public_origin="http://127.0.0.1:5173",
            database_path=Path(":memory:"),
            token_pepper=b"l" * 32,
            bind_host="127.0.0.1",
            environment="test",
        )
    )


def _magic_service(tmp_path: Path, sender: InMemoryEmailSender) -> LabAuthService:
    return LabAuthService(
        LabAuthSettings(
            mode="magic_link",
            public_origin="https://lab.worldeval.example",
            database_path=tmp_path / "lab-auth.sqlite3",
            token_pepper=b"m" * 32,
            allowed_emails=frozenset({"operator@worldeval.example"}),
            bind_host="127.0.0.1",
            environment="production",
        ),
        email_sender=sender,
        clock=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc),
    )


def _app(service: LabAuthService) -> FastAPI:
    app = FastAPI()
    app.state.lab_auth = service
    install_lab_auth_exception_handlers(app)
    app.include_router(create_lab_auth_router(lambda request: request.app.state.lab_auth))
    return app


def test_local_router_sets_an_in_memory_csrf_session_and_enforces_origin() -> None:
    service = _local_service()
    with TestClient(_app(service), base_url="http://testserver") as client:
        configuration = client.get("/api/auth/configuration")
        assert configuration.status_code == 200
        assert configuration.json() == {"mode": "local"}
        assert configuration.headers["cache-control"] == "no-store"
        rejected = client.post(
            "/api/auth/local-login",
            headers={"Origin": "https://evil.example"},
        )
        assert rejected.status_code == 403
        assert rejected.json() == {"detail": {"code": "lab_auth_origin_not_allowed"}}
        assert "evil.example" not in rejected.text

        login = client.post(
            "/api/auth/local-login",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert login.status_code == 200
        assert login.headers["cache-control"] == "no-store"
        assert "worldeval_local_session=" in login.headers["set-cookie"]
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "Secure" not in login.headers["set-cookie"]
        assert login.json()["operator"] == {
            "member_id": 1,
            "email": "local-operator@worldeval.test",
        }
        first_csrf = login.json()["csrf_token"]

        current = client.get("/api/auth/me")
        assert current.status_code == 200
        assert current.json()["operator"] == login.json()["operator"]
        current_csrf = current.json()["csrf_token"]
        assert current_csrf != first_csrf

        invalid_csrf = client.post(
            "/api/auth/csrf/rotate",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "X-WorldEval-CSRF": "A" * 43,
            },
        )
        assert invalid_csrf.status_code == 403
        assert invalid_csrf.json() == {"detail": {"code": "lab_auth_csrf_invalid"}}

        rotated = client.post(
            "/api/auth/csrf/rotate",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "X-WorldEval-CSRF": current_csrf,
            },
        )
        assert rotated.status_code == 200
        final_csrf = rotated.json()["csrf_token"]
        assert final_csrf != current_csrf

        logout = client.post(
            "/api/auth/logout",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "X-WorldEval-CSRF": final_csrf,
            },
        )
        assert logout.status_code == 204
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert client.get("/api/auth/me").status_code == 401


def test_magic_link_router_is_generic_and_callback_never_reflects_its_token(tmp_path: Path) -> None:
    sender = InMemoryEmailSender()
    service = _magic_service(tmp_path, sender)
    with TestClient(_app(service), base_url="https://lab.worldeval.example") as client:
        assert client.get("/api/auth/configuration").json() == {"mode": "magic_link"}
        invited = client.post(
            "/api/auth/magic-link",
            headers={"Origin": "https://lab.worldeval.example"},
            json={"email": "operator@worldeval.example"},
        )
        outsider = client.post(
            "/api/auth/magic-link",
            headers={"Origin": "https://lab.worldeval.example"},
            json={"email": "outsider@worldeval.example"},
        )
        assert invited.status_code == outsider.status_code == 202
        assert invited.json() == outsider.json() == {"accepted": True}
        assert "operator@worldeval.example" not in invited.text
        assert len(sender.messages) == 1

        token = magic_link_token_from_url(sender.messages[0].magic_link_url)
        callback = client.get(
            f"/api/auth/callback?token={token}",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/"
        assert callback.headers["cache-control"] == "no-store"
        assert callback.headers["referrer-policy"] == "no-referrer"
        assert token not in callback.text
        assert token not in callback.headers["location"]
        assert "__Host-worldeval_session=" in callback.headers["set-cookie"]
        assert "Secure" in callback.headers["set-cookie"]

        current = client.get("/api/auth/me")
        assert current.status_code == 200
        assert current.json()["operator"]["email"] == "operator@worldeval.example"
        assert "csrf_token" in current.json()

        invalid_token = "A" * 43
        invalid = client.get(
            f"/api/auth/callback?token={invalid_token}",
            follow_redirects=False,
        )
        assert invalid.status_code == 303
        assert invalid.headers["location"] == "/?auth=invalid-link"
        assert invalid_token not in invalid.text
        assert invalid_token not in invalid.headers["location"]


def test_magic_link_router_rejects_invalid_origin_without_sending_or_reflecting_input(
    tmp_path: Path,
) -> None:
    sender = InMemoryEmailSender()
    service = _magic_service(tmp_path, sender)
    secret_like_email = "operator+private@worldeval.example"
    with TestClient(_app(service), base_url="https://lab.worldeval.example") as client:
        response = client.post(
            "/api/auth/magic-link",
            headers={"Origin": "https://evil.example"},
            json={"email": secret_like_email},
        )
        assert response.status_code == 403
        assert response.json() == {"detail": {"code": "lab_auth_origin_not_allowed"}}
        assert secret_like_email not in response.text
        assert not sender.messages

        malformed = client.post(
            "/api/auth/magic-link",
            headers={
                "Origin": "https://lab.worldeval.example",
                "Content-Type": "application/json",
            },
            content="not-json",
        )
        assert malformed.status_code == 422
        assert malformed.json() == {"detail": {"code": "invalid_lab_auth_request"}}


def test_magic_link_delivery_outage_is_indistinguishable_from_an_unknown_invite(
    tmp_path: Path,
) -> None:
    class FailingSender:
        def send_magic_link(self, _message: object) -> None:
            raise RuntimeError("delivery provider detail must stay private")

    service = LabAuthService(
        LabAuthSettings(
            mode="magic_link",
            public_origin="https://lab.worldeval.example",
            database_path=tmp_path / "lab-auth.sqlite3",
            token_pepper=b"f" * 32,
            allowed_emails=frozenset({"operator@worldeval.example"}),
            bind_host="127.0.0.1",
            environment="production",
        ),
        email_sender=FailingSender(),
    )
    with TestClient(_app(service), base_url="https://lab.worldeval.example") as client:
        invited = client.post(
            "/api/auth/magic-link",
            headers={"Origin": "https://lab.worldeval.example"},
            json={"email": "operator@worldeval.example"},
        )
        unknown = client.post(
            "/api/auth/magic-link",
            headers={"Origin": "https://lab.worldeval.example"},
            json={"email": "unknown@worldeval.example"},
        )
    assert invited.status_code == unknown.status_code == 202
    assert invited.json() == unknown.json() == {"accepted": True}
    assert "delivery provider detail" not in invited.text


def test_team_lab_requirement_requires_a_session_and_csrf_for_mutations() -> None:
    service = _local_service()
    app = _app(service)
    private_router = APIRouter(
        prefix="/api/private-lab",
        dependencies=[
            Depends(create_lab_operator_requirement(lambda request: request.app.state.lab_auth))
        ],
    )

    @private_router.get("/runs")
    async def list_runs() -> dict[str, bool]:
        return {"ok": True}

    @private_router.post("/runs")
    async def create_run() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(private_router)
    with TestClient(app, base_url="http://testserver") as client:
        assert client.get("/api/private-lab/runs").status_code == 401
        login = client.post(
            "/api/auth/local-login",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert login.status_code == 200
        csrf_token = login.json()["csrf_token"]
        assert client.get("/api/private-lab/runs").json() == {"ok": True}
        assert client.post("/api/private-lab/runs").status_code == 403
        accepted = client.post(
            "/api/private-lab/runs",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "X-WorldEval-CSRF": csrf_token,
            },
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"ok": True}


def test_lab_spectator_endpoint_is_behind_the_team_session_boundary() -> None:
    service = _local_service()
    app = _app(service)
    app.include_router(
        lab_router,
        dependencies=[
            Depends(create_lab_operator_requirement(lambda request: request.app.state.lab_auth))
        ],
    )
    with TestClient(app, base_url="http://testserver") as client:
        unauthenticated = client.get("/api/lab/runs/run_labyrinth_unknown/spectator")
    assert unauthenticated.status_code == 401


def test_legacy_live_maze_routes_cannot_bypass_the_team_lab_boundary() -> None:
    service = _local_service()
    app = FastAPI()
    app.state.lab_auth = service
    app.include_router(create_lab_auth_router(lambda request: request.app.state.lab_auth))
    app.include_router(embodiment_router)
    with TestClient(app, base_url="http://testserver") as client:
        anonymous_requests = (
            ("POST", "/api/embodiment/maze-races", {}),
            ("GET", "/api/embodiment/maze-races/ep_live_labyrinth_hidden", None),
            ("GET", "/api/embodiment/maze-races/ep_live_labyrinth_hidden/video", None),
            ("GET", "/api/embodiment/maze-races/ep_live_labyrinth_hidden/replay", None),
            ("POST", "/api/embodiment/maze-races/ep_live_labyrinth_hidden/cancel", {}),
        )
        for method, path, body in anonymous_requests:
            response = client.request(method, path, json=body)
            # Unsafe requests with no Origin fail at CSRF/origin validation before they can reach
            # the live authority; safe reads fail at session authentication.
            assert response.status_code in {401, 403}

        login = client.post(
            "/api/auth/local-login",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert login.status_code == 200
        missing_csrf = client.post(
            "/api/embodiment/maze-races/ep_live_labyrinth_hidden/cancel",
            json={},
        )
        assert missing_csrf.status_code == 403
