from __future__ import annotations

import asyncio
import socket
import tempfile
import unittest
import uuid
from pathlib import Path

try:
    from aiohttp.test_utils import AioHTTPTestCase

    from arksupport.accounts import AccountStore
    from arksupport.auth import generate_token, hash_password, token_digest
    from arksupport.services import WorkbookService
    from arksupport.standalone import StandaloneWebServer
    from arksupport.storage import SupportStore

    HAS_AIOHTTP = True
except ImportError:
    AioHTTPTestCase = unittest.IsolatedAsyncioTestCase
    HAS_AIOHTTP = False


class _Logger:
    def __init__(self):
        self.exceptions = []

    def info(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        self.exceptions.append((args, kwargs))


@unittest.skipUnless(HAS_AIOHTTP, "aiohttp is provided by the AstrBot runtime")
class StandaloneLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "arksupport.sqlite3"
        self.support_store = SupportStore(database_path)
        self.support_store.initialize()
        self.account_store = AccountStore(database_path)
        self.account_store.initialize()
        self.logger = _Logger()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def create_server(self, port: int) -> StandaloneWebServer:
        return StandaloneWebServer(
            support_store=self.support_store,
            account_store=self.account_store,
            workbook_service=WorkbookService(
                self.support_store,
                asyncio.Lock(),
            ),
            static_dir=Path(__file__).parents[1] / "standalone",
            logger=self.logger,
            host="127.0.0.1",
            port=port,
            secure_cookie=False,
        )

    async def test_start_and_stop(self) -> None:
        server = self.create_server(0)
        await server.start()
        self.assertIsNotNone(server.runner)
        self.assertIsNotNone(server.site)
        await server.stop()
        self.assertIsNone(server.runner)
        self.assertIsNone(server.site)

    async def test_port_conflict_is_logged_without_raising(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        try:
            server = self.create_server(port)
            await server.start()
            self.assertIsNone(server.runner)
            self.assertTrue(self.logger.exceptions)
        finally:
            listener.close()


@unittest.skipUnless(HAS_AIOHTTP, "aiohttp is provided by the AstrBot runtime")
class StandaloneApiTest(AioHTTPTestCase):
    async def get_application(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = (
            Path(self.temporary_directory.name) / "arksupport.sqlite3"
        )
        self.support_store = SupportStore(database_path)
        self.support_store.initialize()
        self.account_store = AccountStore(database_path)
        self.account_store.initialize()
        self.workbook_service = WorkbookService(
            self.support_store,
            asyncio.Lock(),
        )
        self.server = StandaloneWebServer(
            support_store=self.support_store,
            account_store=self.account_store,
            workbook_service=self.workbook_service,
            static_dir=Path(__file__).parents[1] / "standalone",
            logger=_Logger(),
            host="127.0.0.1",
            port=0,
            secure_cookie=False,
        )
        return self.server._application()

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.temporary_directory.cleanup()

    def create_invite(self) -> str:
        code = generate_token()
        self.account_store.create_invite(
            invite_id=uuid.uuid4().hex,
            code_hash=token_digest(code),
            creator_user_id=None,
        )
        return code

    async def register_and_login(self, username: str = "test-user") -> str:
        response = await self.client.post(
            "/api/auth/register",
            json={
                "invite_code": self.create_invite(),
                "username": username,
                "password": "registered password",
            },
        )
        self.assertEqual(response.status, 201)
        response = await self.client.post(
            "/api/auth/login",
            json={
                "username": username,
                "password": "registered password",
            },
        )
        self.assertEqual(response.status, 200)
        return (await response.json())["csrf_token"]

    async def test_csrf_and_group_access(self) -> None:
        csrf = await self.register_and_login()
        response = await self.client.post(
            "/api/groups/link",
            json={"umo": "bot:GroupMessage:10001", "remark": "我的备注"},
        )
        self.assertEqual(response.status, 403)

        response = await self.client.post(
            "/api/groups/link",
            json={"umo": "bot:GroupMessage:10001", "remark": "我的备注"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status, 201)
        response = await self.client.get("/api/groups")
        payload = await response.json()
        self.assertEqual(payload["groups"][0]["remark"], "我的备注")

    async def test_temporary_password_requires_change(self) -> None:
        admin = self.account_store.create_user(
            username="admin-user",
            password_hash=hash_password("administrator password"),
            role="admin",
        )
        response = await self.client.post(
            "/api/auth/login",
            json={
                "username": admin["username"],
                "password": "administrator password",
            },
        )
        admin_csrf = (await response.json())["csrf_token"]
        response = await self.client.post(
            "/api/admin/users",
            json={"username": "created-user"},
            headers={"X-CSRF-Token": admin_csrf},
        )
        payload = await response.json()
        temporary_password = payload["temporary_password"]

        await self.client.post(
            "/api/auth/logout",
            json={},
            headers={"X-CSRF-Token": admin_csrf},
        )
        response = await self.client.post(
            "/api/auth/login",
            json={
                "username": "created-user",
                "password": temporary_password,
            },
        )
        payload = await response.json()
        user_csrf = payload["csrf_token"]
        self.assertTrue(payload["user"]["must_change_password"])

        response = await self.client.get("/api/groups")
        self.assertEqual(response.status, 403)
        response = await self.client.post(
            "/api/auth/change-password",
            json={
                "current_password": temporary_password,
                "new_password": "new permanent password",
            },
            headers={"X-CSRF-Token": user_csrf},
        )
        self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
