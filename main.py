import asyncio
import hashlib
from collections import defaultdict
from difflib import get_close_matches
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import (
    PluginUploadFile,
    error_response,
    json_response,
    request,
)
from astrbot.core.star.filter.command import GreedyStr

from .arksupport.parser import normalize_operator_name, parse_workbook
from .arksupport.storage import SupportStore

PLUGIN_NAME = "astrbot_plugin_arksupport"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_QUERY_RESULTS = 20


class ArkSupportPlugin(Star):
    """Query group-scoped Arknights support workbooks."""

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.store = SupportStore(data_dir / "arksupport.sqlite3")
        self.store.initialize()
        self._write_lock = asyncio.Lock()

        context.register_web_api(
            f"/{PLUGIN_NAME}/groups",
            self.web_groups,
            ["GET"],
            "List registered support groups",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/groups/manual",
            self.web_add_manual_group,
            ["POST"],
            "Add a group binding from a UMO",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/groups/<binding_id>/remark",
            self.web_update_group_remark,
            ["POST"],
            "Update a group UMO remark",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/groups/<binding_id>/delete",
            self.web_delete_group,
            ["POST"],
            "Delete a registered support group",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/tables",
            self.web_tables,
            ["GET"],
            "List imported support workbooks",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/tables/import/<binding_id>",
            self.web_import_workbook,
            ["POST"],
            "Import a support workbook",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/tables/<workbook_id>/replace",
            self.web_replace_workbook,
            ["POST"],
            "Replace a support workbook",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/tables/<workbook_id>/delete",
            self.web_delete_workbook,
            ["POST"],
            "Delete a support workbook",
        )

    @filter.command("助战登记")
    async def register_support_group(self, event: AstrMessageEvent):
        """Register the current group so it can be selected in WebUI."""
        if event.is_private_chat() or not event.get_group_id():
            yield event.plain_result("助战登记只能在目标群聊中执行。")
            return

        group = getattr(event.message_obj, "group", None)
        group_name = str(getattr(group, "group_name", "") or "").strip()
        if not group_name:
            group_name = f"群 {event.get_group_id()}"

        async with self._write_lock:
            binding = await asyncio.to_thread(
                self.store.register_group,
                umo=event.unified_msg_origin,
                platform_id=event.get_platform_id(),
                group_id=event.get_group_id(),
                group_name=group_name,
            )
        yield event.plain_result(
            f"已登记“{binding['group_name']}”（{binding['group_id']}）。"
            "现在可在 AstrBot WebUI 的插件页面选择该群并上传助战表。"
        )

    @filter.command("助战")
    async def query_support(
        self,
        event: AstrMessageEvent,
        operator_name: GreedyStr,
    ):
        """Query who provides one operator in the current group."""
        if event.is_private_chat() or not event.get_group_id():
            yield event.plain_result("助战查询仅支持群聊。")
            return

        requested_name = str(operator_name).strip()
        normalized_name = normalize_operator_name(requested_name)
        if not normalized_name:
            yield event.plain_result("用法：/助战 干员名")
            return

        result = await asyncio.to_thread(
            self.store.query_operator,
            event.unified_msg_origin,
            normalized_name,
        )
        if not result["registered"]:
            yield event.plain_result(
                "本群尚未登记。请先在群内执行 /助战登记，"
                "再由管理员到插件 WebUI 上传助战表。"
            )
            return
        if result["workbook_count"] == 0:
            yield event.plain_result("本群尚未上传助战表。")
            return
        if not result["matched_names"]:
            catalog: dict[str, list[str]] = defaultdict(list)
            for item in result["available_names"]:
                catalog[item["normalized_name"]].append(item["operator_name"])
            close_names = get_close_matches(
                normalized_name,
                list(catalog),
                n=5,
                cutoff=0.45,
            )
            suggestions: list[str] = []
            for close_name in close_names:
                for display_name in catalog[close_name]:
                    if display_name not in suggestions:
                        suggestions.append(display_name)
            if suggestions:
                yield event.plain_result(
                    f"未找到“{requested_name}”。你可能想查询："
                    + "、".join(suggestions[:5])
                )
            else:
                yield event.plain_result(f"未找到干员“{requested_name}”。")
            return
        if not result["entries"]:
            display_name = "、".join(result["matched_names"])
            yield event.plain_result(f"{display_name} 当前没有助战记录。")
            return

        unique_entries: list[dict] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for entry in result["entries"]:
            key = (
                entry["server"],
                entry["account"],
                entry["training"],
                entry["source_group_name"],
                entry["note"],
            )
            if key in seen:
                continue
            seen.add(key)
            unique_entries.append(entry)

        grouped: dict[str, list[dict]] = defaultdict(list)
        for entry in unique_entries[:MAX_QUERY_RESULTS]:
            grouped[entry["server"]].append(entry)

        display_name = "、".join(result["matched_names"])
        lines = [f"{display_name} 的助战提供者："]
        for server, entries in grouped.items():
            lines.append(f"【{server}】")
            for entry in entries:
                details = [entry["account"]]
                if entry["training"]:
                    details.append(f"练度：{entry['training']}")
                if entry["source_group_name"]:
                    details.append(f"群：{entry['source_group_name']}")
                if entry["note"]:
                    details.append(f"备注：{entry['note']}")
                lines.append("- " + "｜".join(details))

        omitted = len(unique_entries) - MAX_QUERY_RESULTS
        if omitted > 0:
            lines.append(f"另有 {omitted} 条结果未展示。")
        yield event.plain_result("\n".join(lines))

    async def web_groups(self):
        """List groups registered by the chat command."""
        groups = await asyncio.to_thread(self.store.list_groups)
        return json_response({"groups": groups})

    async def web_add_manual_group(self):
        """Add or update a group from a manually entered UMO."""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求内容必须是 JSON object。")
        umo = payload.get("umo")
        remark = payload.get("remark", "")
        if not isinstance(umo, str) or not isinstance(remark, str):
            return error_response("umo 和 remark 必须是字符串。")
        try:
            async with self._write_lock:
                group = await asyncio.to_thread(
                    self.store.add_manual_group,
                    umo=umo,
                    remark=remark,
                )
            return json_response({"group": group})
        except ValueError as exc:
            return error_response(str(exc))

    async def web_update_group_remark(self, binding_id: str):
        """Update the custom remark for a registered UMO."""
        payload = await request.json(default={})
        if not isinstance(payload, dict) or not isinstance(
            payload.get("remark", ""),
            str,
        ):
            return error_response("remark 必须是字符串。")
        try:
            async with self._write_lock:
                group = await asyncio.to_thread(
                    self.store.update_group_remark,
                    binding_id,
                    payload.get("remark", ""),
                )
            if not group:
                return error_response("指定群不存在。", status_code=404)
            return json_response({"group": group})
        except ValueError as exc:
            return error_response(str(exc))

    async def web_delete_group(self, binding_id: str):
        """Delete a group and all of its imported workbooks."""
        async with self._write_lock:
            deleted = await asyncio.to_thread(self.store.delete_group, binding_id)
        if not deleted:
            return error_response("指定群不存在。", status_code=404)
        return json_response({"deleted": True})

    async def web_tables(self):
        """List workbooks for the selected group."""
        binding_id = str(request.query.get("group_id", "")).strip()
        if not binding_id:
            return error_response("缺少 group_id。")
        workbooks = await asyncio.to_thread(self.store.list_workbooks, binding_id)
        return json_response({"workbooks": workbooks})

    async def _read_uploaded_workbook(self) -> tuple[str, bytes]:
        files = await request.files()
        upload: PluginUploadFile | None = files.get("file")
        if not isinstance(upload, PluginUploadFile):
            raise ValueError("请选择要上传的 Excel 文件。")

        filename = Path(upload.filename or "").name
        if not filename or Path(filename).suffix.lower() != ".xlsx":
            raise ValueError("仅支持 .xlsx 文件。")
        if upload.content_length and upload.content_length > MAX_UPLOAD_BYTES:
            raise ValueError("文件不能超过 10 MiB。")

        await upload.seek(0)
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("文件不能超过 10 MiB。")
        if not content:
            raise ValueError("上传文件为空。")
        return filename, content

    async def _import_uploaded(
        self,
        *,
        binding_id: str,
        workbook_id: str | None = None,
    ):
        try:
            filename, content = await self._read_uploaded_workbook()
            imported = await asyncio.to_thread(parse_workbook, content)
            digest = hashlib.sha256(content).hexdigest()
            async with self._write_lock:
                saved = await asyncio.to_thread(
                    self.store.import_workbook,
                    binding_id=binding_id,
                    filename=filename,
                    sha256=digest,
                    imported=imported,
                    workbook_id=workbook_id,
                )
            return json_response(saved)
        except ValueError as exc:
            return error_response(str(exc))
        except Exception:
            self.logger.exception("Failed to import support workbook.")
            return error_response("导入失败，请检查 AstrBot 日志。", status_code=500)

    async def web_import_workbook(self, binding_id: str):
        """Import a new workbook for a registered group."""
        return await self._import_uploaded(binding_id=binding_id)

    async def web_replace_workbook(self, workbook_id: str):
        """Replace one workbook while preserving its identity."""
        binding_id = await asyncio.to_thread(
            self.store.get_workbook_group_id,
            workbook_id,
        )
        if not binding_id:
            return error_response("要替换的工作簿不存在。", status_code=404)
        return await self._import_uploaded(
            binding_id=binding_id,
            workbook_id=workbook_id,
        )

    async def web_delete_workbook(self, workbook_id: str):
        """Delete one imported workbook."""
        async with self._write_lock:
            deleted = await asyncio.to_thread(
                self.store.delete_workbook,
                workbook_id,
            )
        if not deleted:
            return error_response("指定工作簿不存在。", status_code=404)
        return json_response({"deleted": True})
