"""Shared application services used by both web transports."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from .parser import parse_workbook
from .storage import SupportStore

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class WorkbookService:
    """Validate, parse, and persist workbook uploads transactionally."""

    def __init__(
        self,
        store: SupportStore,
        write_lock: asyncio.Lock,
    ) -> None:
        self.store = store
        self.write_lock = write_lock

    @staticmethod
    def validate_upload(filename: str, content: bytes) -> str:
        safe_name = Path(filename or "").name
        if not safe_name or Path(safe_name).suffix.lower() != ".xlsx":
            raise ValueError("仅支持 .xlsx 文件。")
        if not content:
            raise ValueError("上传文件为空。")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("文件不能超过 10 MiB。")
        return safe_name

    async def import_bytes(
        self,
        *,
        binding_id: str,
        filename: str,
        content: bytes,
        workbook_id: str | None = None,
    ) -> dict:
        """Import or replace one workbook from raw bytes."""
        safe_name = self.validate_upload(filename, content)
        imported = await asyncio.to_thread(parse_workbook, content)
        digest = hashlib.sha256(content).hexdigest()
        async with self.write_lock:
            return await asyncio.to_thread(
                self.store.import_workbook,
                binding_id=binding_id,
                filename=safe_name,
                sha256=digest,
                imported=imported,
                workbook_id=workbook_id,
            )
