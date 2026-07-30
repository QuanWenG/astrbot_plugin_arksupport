"""Pure text formatting helpers for support query results."""

from __future__ import annotations

from collections.abc import Mapping


def format_support_entry(entry: Mapping[str, object]) -> str:
    """Format one support provider using the group template convention."""
    details = [str(entry.get("account", "") or "").strip()]

    training = str(entry.get("training", "") or "").strip()
    if training:
        details.append(f"练度：{training}")

    member_nickname = str(entry.get("member_nickname", "") or "").strip()
    if member_nickname:
        details.append(f"群id：{member_nickname}")

    note = str(entry.get("note", "") or "").strip()
    if note:
        details.append(f"备注：{note}")

    return "｜".join(details)


def append_data_update_footer(message: str, imported_at: object) -> str:
    """Append the latest workbook import date to a query message."""
    timestamp = str(imported_at or "").strip()
    update_date = timestamp.split("T", 1)[0]
    if not update_date:
        return message
    return f"{message}\n\n数据最后更新时间：{update_date}"
