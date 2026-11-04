"""
LemGendary Environment Manager — browser-origin guard tests.

Checks the shared security contract: only the AI Studio GUI origins may call
the sidecar from a browser, browser writes need the client header, and
non-browser clients (no Origin header) keep working.
"""

import unittest

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from env_manager.security import (
    ALLOWED_ORIGINS,
    CLIENT_HEADER,
    CLIENT_HEADER_VALUE,
    install_browser_guard,
    origin_guard_middleware,
)
from env_manager.server import app as server_app

GUI_ORIGIN = "http://tauri.localhost"
FOREIGN_ORIGIN = "https://evil.example"


def _build_app() -> FastAPI:
    """Small app wired exactly like env_manager.server.app."""
    test_app = FastAPI()

    @test_app.get("/read")
    async def read() -> dict:
        return {"ok": True}

    @test_app.post("/write")
    async def write() -> dict:
        return {"ok": True}

    @test_app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    install_browser_guard(test_app)
    return test_app


class TestOriginGuard(unittest.TestCase):
    """Behaviour of origin_guard_middleware together with CORS."""

    def setUp(self) -> None:
        self.client = TestClient(_build_app())

    def test_no_origin_read_allowed(self) -> None:
        """A read with no Origin header is allowed."""
        self.assertEqual(self.client.get("/read").status_code, 200)

    def test_no_origin_write_allowed(self) -> None:
        """A write with no Origin header is allowed."""
        self.assertEqual(self.client.post("/write").status_code, 200)

    def test_gui_origin_read_allowed(self) -> None:
        """A read from the GUI origin is allowed."""
        resp = self.client.get("/read", headers={"Origin": GUI_ORIGIN})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), GUI_ORIGIN)

    def test_foreign_origin_read_refused(self) -> None:
        """A read from a foreign origin is refused."""
        resp = self.client.get("/read", headers={"Origin": FOREIGN_ORIGIN})
        self.assertEqual(resp.status_code, 403)
        self.assertIn("not allowed", resp.json()["detail"])

    def test_null_origin_refused(self) -> None:
        """The ``null`` origin is refused."""
        resp = self.client.get("/read", headers={"Origin": "null"})
        self.assertEqual(resp.status_code, 403)

    def test_gui_origin_write_without_header_refused(self) -> None:
        """A GUI write without the client header is refused."""
        resp = self.client.post("/write", headers={"Origin": GUI_ORIGIN})
        self.assertEqual(resp.status_code, 403)
        self.assertIn(CLIENT_HEADER, resp.json()["detail"])

    def test_gui_origin_write_with_wrong_header_refused(self) -> None:
        """A GUI write with a wrong client header is refused."""
        resp = self.client.post(
            "/write", headers={"Origin": GUI_ORIGIN, CLIENT_HEADER: "other"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_gui_origin_write_with_header_allowed(self) -> None:
        """A GUI write with the client header is allowed."""
        resp = self.client.post(
            "/write", headers={"Origin": GUI_ORIGIN, CLIENT_HEADER: CLIENT_HEADER_VALUE}
        )
        self.assertEqual(resp.status_code, 200)

    def test_foreign_origin_write_with_header_refused(self) -> None:
        """A foreign write is refused even with the client header."""
        resp = self.client.post(
            "/write",
            headers={"Origin": FOREIGN_ORIGIN, CLIENT_HEADER: CLIENT_HEADER_VALUE},
        )
        self.assertEqual(resp.status_code, 403)

    def test_preflight_from_gui_allows_client_header(self) -> None:
        """A GUI preflight is allowed to send the client header."""
        resp = self.client.options(
            "/write",
            headers={
                "Origin": GUI_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": CLIENT_HEADER,
            },
        )
        self.assertEqual(resp.status_code, 200)
        allowed = resp.headers.get("access-control-allow-headers", "").lower()
        self.assertIn(CLIENT_HEADER.lower(), allowed)

    def test_preflight_from_foreign_origin_refused(self) -> None:
        """A preflight from a foreign origin is refused."""
        resp = self.client.options(
            "/write",
            headers={
                "Origin": FOREIGN_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_websocket_gui_origin_allowed(self) -> None:
        """A WebSocket from the GUI origin is accepted."""
        with self.client.websocket_connect("/ws", headers={"Origin": GUI_ORIGIN}) as ws:
            self.assertEqual(ws.receive_json(), {"ok": True})

    def test_websocket_no_origin_allowed(self) -> None:
        """A WebSocket with no Origin is accepted."""
        with self.client.websocket_connect("/ws") as ws:
            self.assertEqual(ws.receive_json(), {"ok": True})

    def test_websocket_foreign_origin_refused(self) -> None:
        """A WebSocket from a foreign origin is refused."""
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "/ws", headers={"Origin": FOREIGN_ORIGIN}
            ) as ws:
                ws.receive_json()


class TestServerWiring(unittest.TestCase):
    """The real sidecar app uses the guard and a closed CORS allow-list."""

    def test_server_middleware_configuration(self) -> None:
        """The real app runs the origin guard outermost and a closed CORS list."""
        classes = [m.cls for m in server_app.user_middleware]
        self.assertIn(origin_guard_middleware, classes)
        self.assertIn(CORSMiddleware, classes)
        # user_middleware lists the outermost middleware first.
        self.assertIs(classes[0], origin_guard_middleware)

        cors = next(m for m in server_app.user_middleware if m.cls is CORSMiddleware)
        allow_origins = cors.kwargs["allow_origins"]
        assert isinstance(allow_origins, list)
        self.assertEqual(allow_origins, list(ALLOWED_ORIGINS))
        self.assertNotIn("*", allow_origins)
        self.assertFalse(cors.kwargs["allow_credentials"])

    def test_real_app_refuses_foreign_origin(self) -> None:
        """The real app refuses a foreign-origin POST with 403."""
        # No context manager: the lifespan (which starts sidecars) does not run.
        client = TestClient(server_app)
        resp = client.post(
            "/api/services/start-all", headers={"Origin": FOREIGN_ORIGIN}
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
