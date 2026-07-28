from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .parser import WorkbookImport

MAX_UMO_LENGTH = 512
MAX_GROUP_REMARK_LENGTH = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_group_umo(umo: str) -> tuple[str, str]:
    """Validate a group UMO and return its platform and session identifiers.

    Args:
        umo: AstrBot unified message origin.

    Returns:
        Platform ID and group session ID.

    Raises:
        ValueError: If the UMO is empty, oversized, malformed, or not a group.
    """
    normalized = umo.strip()
    if not normalized:
        raise ValueError("UMO 不能为空。")
    if len(normalized) > MAX_UMO_LENGTH:
        raise ValueError("UMO 不能超过 512 个字符。")
    try:
        platform_id, message_type, session_id = normalized.split(":", 2)
    except ValueError as exc:
        raise ValueError(
            "UMO 格式应为：平台实例:GroupMessage:群会话ID。"
        ) from exc
    if not platform_id.strip() or not session_id.strip():
        raise ValueError("UMO 的平台实例和群会话ID不能为空。")
    if message_type != "GroupMessage":
        raise ValueError("只允许添加 GroupMessage 类型的群聊 UMO。")
    return platform_id, session_id


def validate_group_remark(remark: str) -> str:
    """Normalize and validate a group remark."""
    normalized = remark.strip()
    if len(normalized) > MAX_GROUP_REMARK_LENGTH:
        raise ValueError("UMO 备注不能超过 200 个字符。")
    return normalized


class SupportStore:
    """SQLite persistence for group bindings and imported workbooks."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open a transaction-scoped connection and always close it."""
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create database schema when it does not exist."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS group_bindings (
                    id TEXT PRIMARY KEY,
                    umo TEXT NOT NULL UNIQUE,
                    platform_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    remark TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workbooks (
                    id TEXT PRIMARY KEY,
                    group_binding_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    sheets_json TEXT NOT NULL,
                    operator_count INTEGER NOT NULL,
                    support_count INTEGER NOT NULL,
                    warning_count INTEGER NOT NULL,
                    imported_at TEXT NOT NULL,
                    FOREIGN KEY (group_binding_id)
                        REFERENCES group_bindings(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS operators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workbook_id TEXT NOT NULL,
                    server TEXT NOT NULL,
                    rarity TEXT NOT NULL,
                    profession TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    source_row INTEGER NOT NULL,
                    FOREIGN KEY (workbook_id)
                        REFERENCES workbooks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS supports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operator_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    account TEXT NOT NULL,
                    training TEXT NOT NULL,
                    source_group_name TEXT NOT NULL,
                    note TEXT NOT NULL,
                    FOREIGN KEY (operator_id)
                        REFERENCES operators(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_workbooks_group
                    ON workbooks(group_binding_id);
                CREATE INDEX IF NOT EXISTS idx_operators_workbook_name
                    ON operators(workbook_id, normalized_name);
                CREATE INDEX IF NOT EXISTS idx_supports_operator
                    ON supports(operator_id);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(group_bindings)"
                ).fetchall()
            }
            if "remark" not in columns:
                connection.execute(
                    "ALTER TABLE group_bindings "
                    "ADD COLUMN remark TEXT NOT NULL DEFAULT ''"
                )

    def register_group(
        self,
        *,
        umo: str,
        platform_id: str,
        group_id: str,
        group_name: str,
    ) -> dict[str, Any]:
        """Create or refresh a group binding."""
        timestamp = _now()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT id, created_at FROM group_bindings WHERE umo = ?",
                (umo,),
            ).fetchone()
            if existing:
                binding_id = existing["id"]
                connection.execute(
                    """
                    UPDATE group_bindings
                    SET platform_id = ?, group_id = ?, group_name = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (platform_id, group_id, group_name, timestamp, binding_id),
                )
            else:
                binding_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO group_bindings (
                        id, umo, platform_id, group_id, group_name, remark,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        binding_id,
                        umo,
                        platform_id,
                        group_id,
                        group_name,
                        timestamp,
                        timestamp,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM group_bindings WHERE id = ?",
                (binding_id,),
            ).fetchone()
        return dict(row)

    def add_manual_group(self, *, umo: str, remark: str) -> dict[str, Any]:
        """Create a group binding from a manually entered UMO.

        Existing bindings keep their discovered group name and only update the
        remark.
        """
        normalized_umo = umo.strip()
        normalized_remark = validate_group_remark(remark)
        platform_id, group_id = parse_group_umo(normalized_umo)
        timestamp = _now()

        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM group_bindings WHERE umo = ?",
                (normalized_umo,),
            ).fetchone()
            if existing:
                binding_id = existing["id"]
                connection.execute(
                    """
                    UPDATE group_bindings
                    SET remark = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (normalized_remark, timestamp, binding_id),
                )
            else:
                binding_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO group_bindings (
                        id, umo, platform_id, group_id, group_name, remark,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        binding_id,
                        normalized_umo,
                        platform_id,
                        group_id,
                        f"群 {group_id}",
                        normalized_remark,
                        timestamp,
                        timestamp,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM group_bindings WHERE id = ?",
                (binding_id,),
            ).fetchone()
        return dict(row)

    def update_group_remark(
        self,
        binding_id: str,
        remark: str,
    ) -> dict[str, Any] | None:
        """Update the remark for one registered UMO."""
        normalized_remark = validate_group_remark(remark)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE group_bindings
                SET remark = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized_remark, _now(), binding_id),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM group_bindings WHERE id = ?",
                (binding_id,),
            ).fetchone()
        return dict(row)

    def get_group_by_umo(self, umo: str) -> dict[str, Any] | None:
        """Return one group binding by UMO."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM group_bindings WHERE umo = ?",
                (umo,),
            ).fetchone()
        return dict(row) if row else None

    def list_groups(self) -> list[dict[str, Any]]:
        """List registered groups with workbook counts."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT g.*,
                       COUNT(DISTINCT w.id) AS workbook_count,
                       COALESCE(SUM(w.operator_count), 0) AS operator_count,
                       COALESCE(SUM(w.support_count), 0) AS support_count
                FROM group_bindings g
                LEFT JOIN workbooks w ON w.group_binding_id = g.id
                GROUP BY g.id
                ORDER BY g.group_name COLLATE NOCASE, g.group_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_group(self, binding_id: str) -> bool:
        """Delete a group binding and all imported data."""
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM group_bindings WHERE id = ?",
                (binding_id,),
            )
        return cursor.rowcount > 0

    def list_workbooks(self, binding_id: str) -> list[dict[str, Any]]:
        """List imported workbooks for one group."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, group_binding_id, original_filename, sha256,
                       sheets_json, operator_count, support_count,
                       warning_count, imported_at
                FROM workbooks
                WHERE group_binding_id = ?
                ORDER BY imported_at DESC, original_filename
                """,
                (binding_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["sheets"] = json.loads(item.pop("sheets_json"))
        return result

    def import_workbook(
        self,
        *,
        binding_id: str,
        filename: str,
        sha256: str,
        imported: WorkbookImport,
        workbook_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a workbook or transactionally replace an existing one."""
        timestamp = _now()
        with self._connection() as connection:
            group = connection.execute(
                "SELECT id FROM group_bindings WHERE id = ?",
                (binding_id,),
            ).fetchone()
            if not group:
                raise ValueError("指定群不存在，请先在群内执行 /助战登记。")

            if workbook_id is None:
                workbook_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO workbooks (
                        id, group_binding_id, original_filename, sha256,
                        sheets_json, operator_count, support_count,
                        warning_count, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workbook_id,
                        binding_id,
                        filename,
                        sha256,
                        json.dumps(imported.sheets, ensure_ascii=False),
                        len(imported.operators),
                        len(imported.supports),
                        len(imported.warnings),
                        timestamp,
                    ),
                )
            else:
                existing = connection.execute(
                    """
                    SELECT id FROM workbooks
                    WHERE id = ? AND group_binding_id = ?
                    """,
                    (workbook_id, binding_id),
                ).fetchone()
                if not existing:
                    raise ValueError("要替换的工作簿不存在或不属于指定群。")
                connection.execute(
                    "DELETE FROM operators WHERE workbook_id = ?",
                    (workbook_id,),
                )
                connection.execute(
                    """
                    UPDATE workbooks
                    SET original_filename = ?, sha256 = ?, sheets_json = ?,
                        operator_count = ?, support_count = ?,
                        warning_count = ?, imported_at = ?
                    WHERE id = ?
                    """,
                    (
                        filename,
                        sha256,
                        json.dumps(imported.sheets, ensure_ascii=False),
                        len(imported.operators),
                        len(imported.supports),
                        len(imported.warnings),
                        timestamp,
                        workbook_id,
                    ),
                )

            operator_ids: list[int] = []
            for operator in imported.operators:
                cursor = connection.execute(
                    """
                    INSERT INTO operators (
                        workbook_id, server, rarity, profession,
                        operator_name, normalized_name, source_row
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workbook_id,
                        operator.server,
                        operator.rarity,
                        operator.profession,
                        operator.operator_name,
                        operator.normalized_name,
                        operator.source_row,
                    ),
                )
                operator_ids.append(int(cursor.lastrowid))

            for support in imported.supports:
                connection.execute(
                    """
                    INSERT INTO supports (
                        operator_id, slot, account, training,
                        source_group_name, note
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operator_ids[support.operator_index],
                        support.slot,
                        support.account,
                        support.training,
                        support.group_name,
                        support.note,
                    ),
                )

        return {
            "id": workbook_id,
            "group_binding_id": binding_id,
            "original_filename": filename,
            "sheets": imported.sheets,
            "operator_count": len(imported.operators),
            "support_count": len(imported.supports),
            "warnings": imported.warnings,
            "imported_at": timestamp,
        }

    def get_workbook_group_id(self, workbook_id: str) -> str | None:
        """Return the owner group for a workbook."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT group_binding_id FROM workbooks WHERE id = ?",
                (workbook_id,),
            ).fetchone()
        return str(row["group_binding_id"]) if row else None

    def delete_workbook(self, workbook_id: str) -> bool:
        """Delete one imported workbook."""
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM workbooks WHERE id = ?",
                (workbook_id,),
            )
        return cursor.rowcount > 0

    def query_operator(self, umo: str, normalized_name: str) -> dict[str, Any]:
        """Query exact operator records or return the group's name catalog."""
        with self._connection() as connection:
            group = connection.execute(
                "SELECT id FROM group_bindings WHERE umo = ?",
                (umo,),
            ).fetchone()
            if not group:
                return {
                    "registered": False,
                    "workbook_count": 0,
                    "matched_names": [],
                    "entries": [],
                    "available_names": [],
                }

            binding_id = group["id"]
            workbook_count = connection.execute(
                "SELECT COUNT(*) FROM workbooks WHERE group_binding_id = ?",
                (binding_id,),
            ).fetchone()[0]
            matched_rows = connection.execute(
                """
                SELECT DISTINCT o.operator_name
                FROM operators o
                JOIN workbooks w ON w.id = o.workbook_id
                WHERE w.group_binding_id = ? AND o.normalized_name = ?
                ORDER BY o.operator_name
                """,
                (binding_id, normalized_name),
            ).fetchall()
            entries = connection.execute(
                """
                SELECT o.server, o.operator_name, o.rarity, o.profession,
                       s.account, s.training, s.source_group_name, s.note,
                       w.original_filename
                FROM supports s
                JOIN operators o ON o.id = s.operator_id
                JOIN workbooks w ON w.id = o.workbook_id
                WHERE w.group_binding_id = ? AND o.normalized_name = ?
                ORDER BY o.server, s.account, w.original_filename
                """,
                (binding_id, normalized_name),
            ).fetchall()
            available_rows = connection.execute(
                """
                SELECT DISTINCT o.operator_name, o.normalized_name
                FROM operators o
                JOIN workbooks w ON w.id = o.workbook_id
                WHERE w.group_binding_id = ?
                ORDER BY o.operator_name
                """,
                (binding_id,),
            ).fetchall()

        return {
            "registered": True,
            "workbook_count": int(workbook_count),
            "matched_names": [row["operator_name"] for row in matched_rows],
            "entries": [dict(row) for row in entries],
            "available_names": [
                {
                    "operator_name": row["operator_name"],
                    "normalized_name": row["normalized_name"],
                }
                for row in available_rows
            ],
        }
