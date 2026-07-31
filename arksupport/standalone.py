"""Authenticated aiohttp server for standalone workbook management."""

from __future__ import annotations

import asyncio
import hmac
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from aiohttp import web

from .accounts import AccountStore
from .auth import (
    generate_temporary_password,
    generate_token,
    hash_password,
    token_digest,
    verify_password,
)
from .services import MAX_UPLOAD_BYTES, WorkbookService
from .storage import SupportStore

SESSION_COOKIE = "arksupport_session"
SESSION_KEY = web.RequestKey("arksupport_session_data", object)
PUBLIC_AUTH_PATHS = {"/api/auth/login", "/api/auth/register"}
PASSWORD_CHANGE_PATHS = {
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/change-password",
}


class RateLimiter:
    """Small in-memory sliding-window limiter for auth endpoints."""

    def __init__(self, attempts: int = 5, window_seconds: int = 900) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        events = self._events[key]
        while events and events[0] <= now - self.window_seconds:
            events.popleft()
        if len(events) >= self.attempts:
            return False
        events.append(now)
        return True


class StandaloneWebServer:
    """Lifecycle owner and protected HTTP transport for port 12226."""

    def __init__(
        self,
        *,
        support_store: SupportStore,
        account_store: AccountStore,
        workbook_service: WorkbookService,
        static_dir: Path,
        logger: Any,
        host: str,
        port: int,
        secure_cookie: bool,
    ) -> None:
        self.support_store = support_store
        self.account_store = account_store
        self.workbook_service = workbook_service
        self.static_dir = static_dir
        self.logger = logger
        self.host = host
        self.port = port
        self.secure_cookie = secure_cookie
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.auth_limiter = RateLimiter()

    @web.middleware
    async def _security_middleware(self, request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response(
                {"error": exc.text or exc.reason},
                status=exc.status,
            )
        except ValueError as exc:
            response = web.json_response({"error": str(exc)}, status=400)
        except Exception:
            self.logger.exception("Standalone web request failed.")
            response = web.json_response(
                {"error": "服务器内部错误，请查看 AstrBot 日志。"},
                status=500,
            )
        response.headers.update(
            {
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'"
                ),
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            }
        )
        return response

    @web.middleware
    async def _auth_middleware(self, request, handler):
        request[SESSION_KEY] = None
        raw_token = request.cookies.get(SESSION_COOKIE, "")
        if raw_token:
            request[SESSION_KEY] = await asyncio.to_thread(
                self.account_store.get_session,
                token_digest(raw_token),
            )

        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.path not in PUBLIC_AUTH_PATHS
        ):
            session = request[SESSION_KEY]
            if not session:
                raise web.HTTPUnauthorized(text="请先登录。")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(supplied, session["csrf_token"]):
                raise web.HTTPForbidden(text="CSRF 校验失败，请刷新页面。")
        return await handler(request)

    def _application(self) -> web.Application:
        app = web.Application(
            middlewares=[self._security_middleware, self._auth_middleware],
            client_max_size=MAX_UPLOAD_BYTES + 1024 * 1024,
        )
        routes = [
            web.get("/", self.index),
            web.get("/api/auth/me", self.auth_me),
            web.post("/api/auth/register", self.auth_register),
            web.post("/api/auth/login", self.auth_login),
            web.post("/api/auth/logout", self.auth_logout),
            web.post("/api/auth/change-password", self.auth_change_password),
            web.get("/api/groups", self.groups_list),
            web.post("/api/groups/link", self.groups_link),
            web.patch("/api/groups/{binding_id}/remark", self.groups_remark),
            web.delete("/api/groups/{binding_id}/link", self.groups_unlink),
            web.delete("/api/groups/{binding_id}", self.groups_delete),
            web.get("/api/groups/{binding_id}/workbooks", self.workbooks_list),
            web.post(
                "/api/groups/{binding_id}/workbooks",
                self.workbooks_import,
            ),
            web.post(
                "/api/workbooks/{workbook_id}/replace",
                self.workbooks_replace,
            ),
            web.delete(
                "/api/workbooks/{workbook_id}",
                self.workbooks_delete,
            ),
            web.get("/api/admin/users", self.admin_users),
            web.post("/api/admin/users", self.admin_create_user),
            web.post(
                "/api/admin/users/{user_id}/active",
                self.admin_user_active,
            ),
            web.post(
                "/api/admin/users/{user_id}/reset-password",
                self.admin_reset_password,
            ),
            web.post(
                "/api/admin/users/{user_id}/role",
                self.admin_set_role,
            ),
            web.delete(
                "/api/admin/users/{user_id}",
                self.admin_delete_user,
            ),
            web.get("/api/admin/invites", self.admin_invites),
            web.post("/api/admin/invites", self.admin_create_invite),
            web.delete(
                "/api/admin/invites/{invite_id}",
                self.admin_revoke_invite,
            ),
        ]
        app.add_routes(routes)
        app.router.add_static(
            "/assets/",
            path=self.static_dir,
            show_index=False,
            append_version=True,
        )
        app.router.add_static(
            "/shared/",
            path=self.static_dir.parent / "pages" / "management",
            show_index=False,
            append_version=True,
        )
        return app

    async def start(self) -> None:
        """Start listening; failures are logged and do not escape."""
        if self.runner:
            return
        runner = web.AppRunner(self._application(), access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
        except Exception:
            await runner.cleanup()
            self.logger.exception(
                "无法启动独立助战表站点 %s:%s，机器人功能继续运行。",
                self.host,
                self.port,
            )
            return
        self.runner = runner
        self.site = site
        self.logger.info(
            "独立助战表站点已启动：http://%s:%s",
            self.host,
            self.port,
        )

    async def stop(self) -> None:
        """Stop the standalone listener and release its port."""
        if self.runner:
            await self.runner.cleanup()
        self.site = None
        self.runner = None

    async def index(self, request):
        return web.FileResponse(self.static_dir / "index.html")

    async def _json(self, request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise web.HTTPBadRequest(text="请求内容必须是 JSON object。") from exc
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="请求内容必须是 JSON object。")
        return payload

    def _client_key(self, request, username: str = "") -> str:
        peer = request.remote or "unknown"
        return f"{peer}:{username.casefold()}"

    def _require_user(
        self,
        request,
        *,
        allow_password_change: bool = False,
    ) -> dict[str, Any]:
        session = request[SESSION_KEY]
        if not session:
            raise web.HTTPUnauthorized(text="请先登录。")
        if (
            session["must_change_password"]
            and not allow_password_change
            and request.path not in PASSWORD_CHANGE_PATHS
        ):
            raise web.HTTPForbidden(text="请先修改临时密码。")
        return session

    def _require_admin(self, request) -> dict[str, Any]:
        session = self._require_user(request)
        if session["role"] != "admin":
            raise web.HTTPForbidden(text="需要管理员权限。")
        return session

    async def _issue_session(self, user_id: str) -> tuple[str, str]:
        token = generate_token()
        csrf_token = generate_token(24)
        await asyncio.to_thread(
            self.account_store.create_session,
            user_id=user_id,
            token_hash=token_digest(token),
            csrf_token=csrf_token,
        )
        return token, csrf_token

    def _set_session_cookie(
        self,
        response: web.StreamResponse,
        token: str,
    ) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            secure=self.secure_cookie,
            samesite="Lax",
            path="/",
        )

    async def auth_me(self, request):
        user = self._require_user(request, allow_password_change=True)
        return web.json_response(
            {
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "role": user["role"],
                    "must_change_password": user["must_change_password"],
                },
                "csrf_token": user["csrf_token"],
            }
        )

    async def auth_register(self, request):
        payload = await self._json(request)
        username = str(payload.get("username", ""))
        if not self.auth_limiter.allow(self._client_key(request, username)):
            raise web.HTTPTooManyRequests(text="尝试次数过多，请稍后再试。")
        password_hash = await asyncio.to_thread(
            hash_password,
            str(payload.get("password", "")),
        )
        user = await asyncio.to_thread(
            self.account_store.register_with_invite,
            invite_hash=token_digest(str(payload.get("invite_code", ""))),
            username=username,
            password_hash=password_hash,
        )
        return web.json_response({"user": user}, status=201)

    async def auth_login(self, request):
        payload = await self._json(request)
        username = str(payload.get("username", ""))
        if not self.auth_limiter.allow(self._client_key(request, username)):
            raise web.HTTPTooManyRequests(text="尝试次数过多，请稍后再试。")
        user = await asyncio.to_thread(
            self.account_store.get_user_by_username,
            username,
        )
        password_ok = bool(user) and await asyncio.to_thread(
            verify_password,
            str(payload.get("password", "")),
            user["password_hash"],
        )
        if not password_ok:
            raise web.HTTPUnauthorized(text="用户名或密码错误。")
        if not user["is_active"]:
            raise web.HTTPForbidden(text="账号已被禁用。")
        token, csrf_token = await self._issue_session(user["id"])
        response = web.json_response(
            {
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "role": user["role"],
                    "must_change_password": bool(
                        user["must_change_password"]
                    ),
                },
                "csrf_token": csrf_token,
            }
        )
        self._set_session_cookie(response, token)
        await asyncio.to_thread(self.account_store.mark_login, user["id"])
        return response

    async def auth_logout(self, request):
        self._require_user(request, allow_password_change=True)
        raw_token = request.cookies.get(SESSION_COOKIE, "")
        if raw_token:
            await asyncio.to_thread(
                self.account_store.delete_session,
                token_digest(raw_token),
            )
        response = web.json_response({"logged_out": True})
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    async def auth_change_password(self, request):
        session = self._require_user(request, allow_password_change=True)
        payload = await self._json(request)
        user = await asyncio.to_thread(
            self.account_store.get_user,
            session["id"],
        )
        current_ok = user and await asyncio.to_thread(
            verify_password,
            str(payload.get("current_password", "")),
            user["password_hash"],
        )
        if not current_ok:
            raise web.HTTPBadRequest(text="当前密码错误。")
        new_hash = await asyncio.to_thread(
            hash_password,
            str(payload.get("new_password", "")),
        )
        await asyncio.to_thread(
            self.account_store.set_password,
            session["id"],
            new_hash,
            must_change_password=False,
            allow_admin_target=True,
        )
        token, csrf_token = await self._issue_session(session["id"])
        response = web.json_response(
            {"changed": True, "csrf_token": csrf_token}
        )
        self._set_session_cookie(response, token)
        return response

    async def groups_list(self, request):
        user = self._require_user(request)
        groups = await asyncio.to_thread(
            self.account_store.list_visible_groups,
            user_id=user["id"],
            role=user["role"],
        )
        return web.json_response({"groups": groups})

    async def groups_link(self, request):
        user = self._require_user(request)
        payload = await self._json(request)
        async with self.workbook_service.write_lock:
            group = await asyncio.to_thread(
                self.support_store.ensure_manual_group,
                str(payload.get("umo", "")),
            )
        await asyncio.to_thread(
            self.account_store.link_group,
            user_id=user["id"],
            binding_id=group["id"],
            remark=str(payload.get("remark", "")),
        )
        return web.json_response({"group_id": group["id"]}, status=201)

    async def groups_remark(self, request):
        user = self._require_user(request)
        payload = await self._json(request)
        updated = await asyncio.to_thread(
            self.account_store.update_group_remark,
            user_id=user["id"],
            binding_id=request.match_info["binding_id"],
            remark=str(payload.get("remark", "")),
        )
        if not updated:
            raise web.HTTPNotFound(text="你尚未关联该群。")
        return web.json_response({"updated": True})

    async def groups_unlink(self, request):
        user = self._require_user(request)
        deleted = await asyncio.to_thread(
            self.account_store.unlink_group,
            user_id=user["id"],
            binding_id=request.match_info["binding_id"],
        )
        if not deleted:
            raise web.HTTPNotFound(text="你尚未关联该群。")
        return web.json_response({"unlinked": True})

    async def groups_delete(self, request):
        self._require_admin(request)
        async with self.workbook_service.write_lock:
            deleted = await asyncio.to_thread(
                self.support_store.delete_group,
                request.match_info["binding_id"],
            )
        if not deleted:
            raise web.HTTPNotFound(text="指定群不存在。")
        return web.json_response({"deleted": True})

    async def _require_group_access(self, request, binding_id: str) -> dict:
        user = self._require_user(request)
        allowed = await asyncio.to_thread(
            self.account_store.can_access_group,
            user_id=user["id"],
            role=user["role"],
            binding_id=binding_id,
        )
        if not allowed:
            raise web.HTTPForbidden(text="无权访问该群。")
        return user

    async def workbooks_list(self, request):
        binding_id = request.match_info["binding_id"]
        await self._require_group_access(request, binding_id)
        workbooks = await asyncio.to_thread(
            self.support_store.list_workbooks,
            binding_id,
        )
        return web.json_response({"workbooks": workbooks})

    async def _read_upload(self, request) -> tuple[str, bytes]:
        reader = await request.multipart()
        while field := await reader.next():
            if field.name != "file":
                continue
            filename = field.filename or ""
            content = bytearray()
            while chunk := await field.read_chunk():
                content.extend(chunk)
                if len(content) > MAX_UPLOAD_BYTES:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=MAX_UPLOAD_BYTES,
                        actual_size=len(content),
                    )
            return filename, bytes(content)
        raise web.HTTPBadRequest(text="请选择要上传的 Excel 文件。")

    async def workbooks_import(self, request):
        binding_id = request.match_info["binding_id"]
        await self._require_group_access(request, binding_id)
        filename, content = await self._read_upload(request)
        result = await self.workbook_service.import_bytes(
            binding_id=binding_id,
            filename=filename,
            content=content,
        )
        return web.json_response(result, status=201)

    async def _workbook_binding(self, workbook_id: str) -> str:
        binding_id = await asyncio.to_thread(
            self.support_store.get_workbook_group_id,
            workbook_id,
        )
        if not binding_id:
            raise web.HTTPNotFound(text="指定工作簿不存在。")
        return binding_id

    async def workbooks_replace(self, request):
        workbook_id = request.match_info["workbook_id"]
        binding_id = await self._workbook_binding(workbook_id)
        await self._require_group_access(request, binding_id)
        filename, content = await self._read_upload(request)
        result = await self.workbook_service.import_bytes(
            binding_id=binding_id,
            filename=filename,
            content=content,
            workbook_id=workbook_id,
        )
        return web.json_response(result)

    async def workbooks_delete(self, request):
        workbook_id = request.match_info["workbook_id"]
        binding_id = await self._workbook_binding(workbook_id)
        await self._require_group_access(request, binding_id)
        async with self.workbook_service.write_lock:
            deleted = await asyncio.to_thread(
                self.support_store.delete_workbook,
                workbook_id,
            )
        return web.json_response({"deleted": deleted})

    async def admin_users(self, request):
        self._require_admin(request)
        users = await asyncio.to_thread(
            self.account_store.list_users,
            include_admins=False,
        )
        return web.json_response({"users": users})

    async def admin_create_user(self, request):
        self._require_admin(request)
        payload = await self._json(request)
        temporary_password = generate_temporary_password()
        user = await asyncio.to_thread(
            self.account_store.create_user,
            username=str(payload.get("username", "")),
            password_hash=await asyncio.to_thread(
                hash_password,
                temporary_password,
            ),
            role="user",
            must_change_password=True,
        )
        return web.json_response(
            {"user": user, "temporary_password": temporary_password},
            status=201,
        )

    async def admin_user_active(self, request):
        self._require_admin(request)
        payload = await self._json(request)
        user = await asyncio.to_thread(
            self.account_store.set_user_active,
            request.match_info["user_id"],
            bool(payload.get("active", False)),
            allow_admin_target=False,
        )
        if not user:
            raise web.HTTPNotFound(text="普通账号不存在。")
        return web.json_response({"user": user})

    async def admin_reset_password(self, request):
        self._require_admin(request)
        temporary_password = generate_temporary_password()
        user = await asyncio.to_thread(
            self.account_store.set_password,
            request.match_info["user_id"],
            await asyncio.to_thread(hash_password, temporary_password),
            must_change_password=True,
            allow_admin_target=False,
        )
        if not user:
            raise web.HTTPNotFound(text="普通账号不存在。")
        return web.json_response(
            {"user": user, "temporary_password": temporary_password}
        )

    async def admin_set_role(self, request):
        self._require_admin(request)
        payload = await self._json(request)
        role = str(payload.get("role", ""))
        try:
            user = await asyncio.to_thread(
                self.account_store.set_user_role,
                request.match_info["user_id"],
                role,
            )
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        if not user:
            raise web.HTTPNotFound(text="普通账号不存在。")
        return web.json_response({"user": user})

    async def admin_delete_user(self, request):
        self._require_admin(request)
        deleted = await asyncio.to_thread(
            self.account_store.delete_user,
            request.match_info["user_id"],
        )
        if not deleted:
            raise web.HTTPNotFound(text="无法删除该账号（管理员账号不可删除或账号不存在）。")
        return web.json_response({"deleted": True})

    async def admin_invites(self, request):
        self._require_admin(request)
        invites = await asyncio.to_thread(
            self.account_store.list_invites,
            include_all=True,
        )
        for invite in invites:
            invite.pop("creator_username", None)
        return web.json_response({"invites": invites})

    async def admin_create_invite(self, request):
        user = self._require_admin(request)
        code = generate_token(18)
        invite = await asyncio.to_thread(
            self.account_store.create_invite,
            invite_id=uuid.uuid4().hex,
            code_hash=token_digest(code),
            creator_user_id=user["id"],
        )
        return web.json_response(
            {"invite": invite, "code": code},
            status=201,
        )

    async def admin_revoke_invite(self, request):
        self._require_admin(request)
        revoked = await asyncio.to_thread(
            self.account_store.revoke_invite,
            request.match_info["invite_id"],
            allow_all=True,
        )
        if not revoked:
            raise web.HTTPNotFound(text="有效邀请码不存在。")
        return web.json_response({"revoked": True})
