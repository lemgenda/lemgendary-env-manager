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
    ALLOWED_HEADERS,
    ALLOWED_METHODS,
    ALLOWED_ORIGINS,
    CLIENT_HEADER,
    CLIENT_HEADER_VALUE,
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

    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_credentials=False,
        allow_methods=list(ALLOWED_METHODS),
        allow_headers=list(ALLOWED_HEADERS),
    )
    test_app.add_middleware(origin_guard_middleware)
    return test_app


class TestOriginGuard(unittest.TestCase):
    """Behaviour of origin_guard_middleware together with CORS."""

    def setUp(self) -> None:
        self.client = TestClient(_build_app())

    def test_no_origin_read_allowed(self) -> None:
        self.assertEqual(self.client.get("/read").status_code, 200)

    def test_no_origin_write_allowed(self) -> None:
        self.assertEqual(self.client.post("/write").status_code, 200)

    def test_gui_origin_read_allowed(self) -> None:
        resp = self.client.get("/read", headers={"Origin": GUI_ORIGIN})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), GUI_ORIGIN)

    def test_foreign_origin_read_refused(self) -> None:
        resp = self.client.get("/read", headers={"Origin": FOREIGN_ORIGIN})
        self.assertEqual(resp.status_code, 403)
        self.assertIn("not allowed", resp.json()["detail"])

    def test_null_origin_refused(self) -> None:
        resp = self.client.get("/read", headers={"Origin": "null"})
        self.assertEqual(resp.status_code, 403)

    def test_gui_origin_write_without_header_refused(self) -> None:
        resp = self.client.post("/write", headers={"Origin": GUI_ORIGIN})
        self.assertEqual(resp.status_code, 403)
        self.assertIn(CLIENT_HEADER, resp.json()["detail"])

    def test_gui_origin_write_with_wrong_header_refused(self) -> None:
        resp = self.client.post(
            "/write", headers={"Origin": GUI_ORIGIN, CLIENT_HEADER: "other"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_gui_origin_write_with_header_allowed(self) -> None:
        resp = self.client.post(
            "/write", headers={"Origin": GUI_ORIGIN, CLIENT_HEADER: CLIENT_HEADER_VALUE}
        )
        self.assertEqual(resp.status_code, 200)

    def test_foreign_origin_write_with_header_refused(self) -> None:
        resp = self.client.post(
            "/write",
            headers={"Origin": FOREIGN_ORIGIN, CLIENT_HEADER: CLIENT_HEADER_VALUE},
        )
        self.assertEqual(resp.status_code, 403)

    def test_preflight_from_gui_allows_client_header(self) -> None:
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
        resp = self.client.options(
            "/write",
            headers={
                "Origin": FOREIGN_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_websocket_gui_origin_allowed(self) -> None:
        with self.client.websocket_connect("/ws", headers={"Origin": GUI_ORIGIN}) as ws:
            self.assertEqual(ws.receive_json(), {"ok": True})

    def test_websocket_no_origin_allowed(self) -> None:
        with self.client.websocket_connect("/ws") as ws:
            self.assertEqual(ws.receive_json(), {"ok": True})

    def test_websocket_foreign_origin_refused(self) -> None:
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "/ws", headers={"Origin": FOREIGN_ORIGIN}
            ) as ws:
                ws.receive_json()


class TestServerWiring(unittest.TestCase):
    """The real sidecar app uses the guard and a closed CORS allow-list."""

    def test_server_middleware_configuration(self) -> None:
        classes = [m.cls for m in server_app.user_middleware]
        self.assertIn(origin_guard_middleware, classes)
        self.assertIn(CORSMiddleware, classes)
        # user_middleware lists the outermost middleware first.
        self.assertIs(classes[0], origin_guard_middleware)

        cors = next(m for m in server_app.user_middleware if m.cls is CORSMiddleware)
        self.assertEqual(list(cors.kwargs["allow_origins"]), list(ALLOWED_ORIGINS))
        self.assertNotIn("*", cors.kwargs["allow_origins"])
        self.assertFalse(cors.kwargs["allow_credentials"])

    def test_real_app_refuses_foreign_origin(self) -> None:
        # No context manager: the lifespan (which starts sidecars) does not run.
        client = TestClient(server_app)
        resp = client.post(
            "/api/services/start-all", headers={"Origin": FOREIGN_ORIGIN}
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
