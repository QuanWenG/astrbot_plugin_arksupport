"""Constrained LLM matching helpers for operator names."""

from __future__ import annotations

import json
from collections.abc import Iterable

from .parser import normalize_operator_name


def build_operator_match_prompt(requested_name: str, catalog: Iterable[str]) -> str:
    """Build a prompt that limits the answer to the supplied group catalog."""
    unique_catalog = sorted(
        {str(name).strip() for name in catalog if str(name).strip()}
    )
    return (
        "用户输入了一个《明日方舟》干员名，可能包含同音字、外号或错别字。"
        "请从候选干员名中选择最可能的一项。无法可靠判断时返回 null。"
        "不得返回候选列表之外的名称。\n"
        f"用户输入：{json.dumps(requested_name, ensure_ascii=False)}\n"
        f"候选干员名：{json.dumps(unique_catalog, ensure_ascii=False)}\n"
        '只输出一个 JSON 对象，不要解释：{"match":"候选中的完整名称"}；'
        '无法判断则输出：{"match":null}'
    )


def parse_operator_match(
    response_text: str,
    available_names: Iterable[dict[str, str]],
) -> tuple[str, str] | None:
    """Validate an LLM response against the current group's exact catalog.

    Returns:
        A ``(normalized_name, display_name)`` pair, or ``None`` when the
        response is invalid, uncertain, or outside the supplied catalog.
    """
    text = str(response_text or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    matched_name = payload.get("match")
    if not isinstance(matched_name, str) or not matched_name.strip():
        return None

    allowed: dict[str, tuple[str, str]] = {}
    for item in available_names:
        display_name = str(item.get("operator_name", "")).strip()
        normalized_name = str(item.get("normalized_name", "")).strip()
        if display_name and normalized_name:
            allowed.setdefault(
                normalize_operator_name(display_name),
                (normalized_name, display_name),
            )

    selected = normalize_operator_name(matched_name)
    if selected not in allowed:
        return None
    return allowed[selected]
