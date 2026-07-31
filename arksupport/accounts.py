"""Persistence for standalone-site accounts, sessions, and access grants."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import normalize_username
from .storage import validate_group_remark

SESSION_DAYS = 7
INVITE_DAYS = 7


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _future(days: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).isoformat(timespec="seconds")


class AccountStore:
    """SQLite repository for web identities and group access."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create additive authentication and authorization tables."""
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    normalized_username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user'
                        CHECK (role IN ('user', 'admin')),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS web_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id)
                        REFERENCES web_users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS registration_invites (
                    id TEXT PRIMARY KEY,
                    code_hash TEXT NOT NULL UNIQUE,
                    created_by_user_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    used_at TEXT,
                    used_by_user_id TEXT,
                    FOREIGN KEY (created_by_user_id)
                        REFERENCES web_users(id) ON DELETE SET NULL,
                    FOREIGN KEY (used_by_user_id)
                        REFERENCES web_users(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS user_group_access (
                    user_id TEXT NOT NULL,
                    group_binding_id TEXT NOT NULL,
                    remark TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, group_binding_id),
                    FOREIGN KEY (user_id)
                        REFERENCES web_users(id) ON DELETE CASCADE,
                    FOREIGN KEY (group_binding_id)
                        REFERENCES group_bindings(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_web_sessions_user
                    ON web_sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_invites_creator
                    ON registration_invites(created_by_user_id);
                CREATE INDEX IF NOT EXISTS idx_user_group_binding
                    ON user_group_access(group_binding_id);
                """
            )

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "is_active": bool(row["is_active"]),
            "must_change_password": bool(row["must_change_password"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
        }

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
        must_change_password: bool = False,
    ) -> dict[str, Any]:
        """Create a standalone web account."""
        normalized = normalize_username(username)
        display = username.strip()
        if role not in {"user", "admin"}:
            raise ValueError("无效的账号角色。")
        timestamp = _now()
        user_id = uuid.uuid4().hex
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO web_users (
                        id, username, normalized_username, password_hash,
                        role, is_active, must_change_password,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        user_id,
                        display,
                        normalized,
                        password_hash,
                        role,
                        int(must_change_password),
                        timestamp,
                        timestamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM web_users WHERE id = ?",
                    (user_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在。") from exc
        return self._public_user(row)

    def register_with_invite(
        self,
        *,
        invite_hash: str,
        username: str,
        password_hash: str,
    ) -> dict[str, Any]:
        """Atomically consume an invite and create a regular account."""
        normalized = normalize_username(username)
        timestamp = _now()
        user_id = uuid.uuid4().hex
        try:
            with self._connection() as connection:
                invite = connection.execute(
                    """
                    SELECT * FROM registration_invites
                    WHERE code_hash = ?
                      AND revoked_at IS NULL
                      AND used_at IS NULL
                      AND expires_at > ?
                    """,
                    (invite_hash, timestamp),
                ).fetchone()
                if not invite:
                    raise ValueError("邀请码无效、已使用或已过期。")
                connection.execute(
                    """
                    INSERT INTO web_users (
                        id, username, normalized_username, password_hash,
                        role, is_active, must_change_password,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'user', 1, 0, ?, ?)
                    """,
                    (
                        user_id,
                        username.strip(),
                        normalized,
                        password_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                cursor = connection.execute(
                    """
                    UPDATE registration_invites
                    SET used_at = ?, used_by_user_id = ?
                    WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL
                    """,
                    (timestamp, user_id, invite["id"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("邀请码已被使用。")
                row = connection.execute(
                    "SELECT * FROM web_users WHERE id = ?",
                    (user_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在。") from exc
        return self._public_user(row)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        try:
            normalized = normalize_username(username)
        except ValueError:
            return None
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM web_users WHERE normalized_username = ?",
                (normalized,),
            ).fetchone()
        return dict(row) if row else None

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM web_users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_users(self, *, include_admins: bool) -> list[dict[str, Any]]:
        where = "" if include_admins else "WHERE role = 'user'"
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM web_users
                {where}
                ORDER BY role DESC, normalized_username
                """
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def set_user_active(
        self,
        user_id: str,
        active: bool,
        *,
        allow_admin_target: bool,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM web_users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not row or (row["role"] == "admin" and not allow_admin_target):
                return None
            connection.execute(
                """
                UPDATE web_users
                SET is_active = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(active), _now(), user_id),
            )
            if not active:
                connection.execute(
                    "DELETE FROM web_sessions WHERE user_id = ?",
                    (user_id,),
                )
            updated = connection.execute(
                "SELECT * FROM web_users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return self._public_user(updated)

    def set_user_role(
        self,
        user_id: str,
        role: str,
    ) -> dict[str, Any] | None:
        if role not in {"user", "admin"}:
            raise ValueError("无效的账号角色。")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE web_users
                SET role = ?, updated_at = ?
                WHERE id = ?
                """,
                (role, _now(), user_id),
            )
            if cursor.rowcount == 0:
                return None
            connection.execute(
                "DELETE FROM web_sessions WHERE user_id = ?",
                (user_id,),
            )
            row = connection.execute(
                "SELECT * FROM web_users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return self._public_user(row)

    def set_password(
        self,
        user_id: str,
        password_hash: str,
        *,
        must_change_password: bool,
        allow_admin_target: bool,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM web_users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not row or (row["role"] == "admin" and not allow_admin_target):
                return None
            connection.execute(
                """
                UPDATE web_users
                SET password_hash = ?, must_change_password = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    password_hash,
                    int(must_change_password),
                    _now(),
                    user_id,
                ),
            )
            connection.execute(
                "DELETE FROM web_sessions WHERE user_id = ?",
                (user_id,),
            )
            updated = connection.execute(
                "SELECT * FROM web_users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return self._public_user(updated)

    def mark_login(self, user_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE web_users SET last_login_at = ? WHERE id = ?",
                (_now(), user_id),
            )

    def create_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        csrf_token: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM web_sessions WHERE expires_at <= ?",
                (_now(),),
            )
            connection.execute(
                """
                INSERT INTO web_sessions (
                    token_hash, user_id, csrf_token, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    user_id,
                    csrf_token,
                    _now(),
                    _future(SESSION_DAYS),
                ),
            )

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT s.csrf_token, s.expires_at,
                       u.id, u.username, u.role, u.is_active,
                       u.must_change_password
                FROM web_sessions s
                JOIN web_users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                """,
                (token_hash, _now()),
            ).fetchone()
        if not row or not row["is_active"]:
            return None
        result = dict(row)
        result["is_active"] = bool(result["is_active"])
        result["must_change_password"] = bool(result["must_change_password"])
        return result

    def delete_session(self, token_hash: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM web_sessions WHERE token_hash = ?",
                (token_hash,),
            )

    def create_invite(
        self,
        *,
        invite_id: str,
        code_hash: str,
        creator_user_id: str | None,
    ) -> dict[str, Any]:
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO registration_invites (
                    id, code_hash, created_by_user_id,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    invite_id,
                    code_hash,
                    creator_user_id,
                    timestamp,
                    _future(INVITE_DAYS),
                ),
            )
            row = connection.execute(
                """
                SELECT i.*, u.username AS creator_username
                FROM registration_invites i
                LEFT JOIN web_users u ON u.id = i.created_by_user_id
                WHERE i.id = ?
                """,
                (invite_id,),
            ).fetchone()
        return self._public_invite(row)

    @staticmethod
    def _public_invite(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "creator_username": row["creator_username"] or "AstrBot 超级管理员",
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "used_at": row["used_at"],
            "used_by_user_id": row["used_by_user_id"],
        }

    def list_invites(
        self,
        *,
        creator_user_id: str | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        condition = ""
        parameters: tuple[Any, ...] = ()
        if not include_all:
            condition = "WHERE i.created_by_user_id = ?"
            parameters = (creator_user_id,)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT i.*, u.username AS creator_username
                FROM registration_invites i
                LEFT JOIN web_users u ON u.id = i.created_by_user_id
                {condition}
                ORDER BY i.created_at DESC
                """,
                parameters,
            ).fetchall()
        return [self._public_invite(row) for row in rows]

    def revoke_invite(
        self,
        invite_id: str,
        *,
        creator_user_id: str | None = None,
        allow_all: bool = False,
    ) -> bool:
        where = "id = ?"
        parameters: list[Any] = [invite_id]
        if not allow_all:
            where += " AND created_by_user_id = ?"
            parameters.append(creator_user_id)
        with self._connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE registration_invites
                SET revoked_at = ?
                WHERE {where} AND used_at IS NULL AND revoked_at IS NULL
                """,
                (_now(), *parameters),
            )
        return cursor.rowcount > 0

    def link_group(
        self,
        *,
        user_id: str,
        binding_id: str,
        remark: str,
    ) -> None:
        value = validate_group_remark(remark)
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO user_group_access (
                    user_id, group_binding_id, remark, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, group_binding_id) DO UPDATE
                SET remark = excluded.remark, updated_at = excluded.updated_at
                """,
                (user_id, binding_id, value, timestamp, timestamp),
            )

    def update_group_remark(
        self,
        *,
        user_id: str,
        binding_id: str,
        remark: str,
    ) -> bool:
        value = validate_group_remark(remark)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE user_group_access
                SET remark = ?, updated_at = ?
                WHERE user_id = ? AND group_binding_id = ?
                """,
                (value, _now(), user_id, binding_id),
            )
        return cursor.rowcount > 0

    def unlink_group(self, *, user_id: str, binding_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM user_group_access
                WHERE user_id = ? AND group_binding_id = ?
                """,
                (user_id, binding_id),
            )
        return cursor.rowcount > 0

    def delete_user(self, user_id: str) -> bool:
        "Permanently delete a user, their sessions, invites, and group access."
        with self._connection() as connection:
            connection.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM registration_invites WHERE created_by_user_id = ? OR used_by_user_id = ?", (user_id, user_id))
            connection.execute("DELETE FROM user_group_access WHERE user_id = ?", (user_id,))
            cursor = connection.execute("DELETE FROM web_users WHERE id = ? AND role != 'admin'", (user_id,))
        return cursor.rowcount > 0

    def can_access_group(
        self,
        *,
        user_id: str,
        role: str,
        binding_id: str,
    ) -> bool:
        if role == "admin":
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT 1 FROM group_bindings WHERE id = ?",
                    (binding_id,),
                ).fetchone()
            return bool(row)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM user_group_access
                WHERE user_id = ? AND group_binding_id = ?
                """,
                (user_id, binding_id),
            ).fetchone()
        return bool(row)

    def list_visible_groups(
        self,
        *,
        user_id: str,
        role: str,
    ) -> list[dict[str, Any]]:
        condition = "" if role == "admin" else "WHERE own.user_id IS NOT NULL"
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT g.id, g.umo, g.platform_id, g.group_id, g.group_name,
                       COALESCE(own.remark, '') AS remark,
                       CASE WHEN own.user_id IS NULL THEN 0 ELSE 1 END AS is_linked,
                       COUNT(DISTINCT w.id) AS workbook_count,
                       COALESCE(SUM(w.operator_count), 0) AS operator_count,
                       COALESCE(SUM(w.support_count), 0) AS support_count
                FROM group_bindings g
                LEFT JOIN user_group_access own
                  ON own.group_binding_id = g.id AND own.user_id = ?
                LEFT JOIN workbooks w ON w.group_binding_id = g.id
                {condition}
                GROUP BY g.id
                ORDER BY g.group_name COLLATE NOCASE, g.group_id
                """,
                (user_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["is_linked"] = bool(item["is_linked"])
        return result
