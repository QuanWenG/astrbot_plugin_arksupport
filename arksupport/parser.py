from __future__ import annotations

import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from io import BytesIO
from zipfile import BadZipFile, ZipFile

SLOT_HEADER = re.compile(r"(账号|练度|群成员昵称|备注)(\d+)")
CELL_REFERENCE = re.compile(r"([A-Z]+)")
BASE_HEADERS = ("稀有度", "职业", "干员名")
MAX_HEADER_SCAN_ROWS = 20
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True)
class OperatorRecord:
    """One operator row from a workbook."""

    server: str
    rarity: str
    profession: str
    operator_name: str
    normalized_name: str
    source_row: int


@dataclass(frozen=True)
class SupportRecord:
    """One populated numbered support slot."""

    operator_index: int
    slot: int
    account: str
    training: str
    member_nickname: str
    note: str


@dataclass
class WorkbookImport:
    """Normalized workbook content ready for persistence."""

    sheets: list[str] = field(default_factory=list)
    operators: list[OperatorRecord] = field(default_factory=list)
    supports: list[SupportRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_operator_name(value: str) -> str:
    """Normalize an operator name for exact lookup.

    Args:
        value: Original operator name.

    Returns:
        NFKC-normalized, case-folded text without whitespace.
    """
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(normalized.split())


def _cell_text(value: object) -> str:
    """Convert an XLSX value to trimmed display text."""
    if value is None:
        return ""
    return str(value).strip()


def _column_index(reference: str) -> int:
    """Convert an A1 cell reference to a zero-based column index."""
    match = CELL_REFERENCE.match(reference.upper())
    if not match:
        raise ValueError(f"无效的单元格引用：{reference}")
    result = 0
    for character in match.group(1):
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _shared_strings(archive: ZipFile) -> list[str]:
    """Read the optional XLSX shared string table."""
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
        for item in root.findall(f"{{{MAIN_NS}}}si")
    ]


def _sheet_definitions(archive: ZipFile) -> list[tuple[str, str]]:
    """Resolve workbook sheet names to worksheet XML paths."""
    workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    relations_root = ET.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    targets = {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in relations_root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }

    definitions: list[tuple[str, str]] = []
    sheets = workbook_root.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        return definitions
    for sheet in sheets.findall(f"{{{MAIN_NS}}}sheet"):
        relation_id = sheet.attrib.get(f"{{{REL_NS}}}id")
        target = targets.get(relation_id or "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        definitions.append((sheet.attrib.get("name", "Sheet"), path))
    return definitions


def _worksheet_rows(
    archive: ZipFile,
    path: str,
    shared_strings: list[str],
) -> list[tuple[int, tuple[str, ...]]]:
    """Read sparse worksheet XML into row-numbered text tuples."""
    root = ET.fromstring(archive.read(path))
    sheet_data = root.find(f"{{{MAIN_NS}}}sheetData")
    if sheet_data is None:
        return []

    result: list[tuple[int, tuple[str, ...]]] = []
    for fallback_row, row in enumerate(
        sheet_data.findall(f"{{{MAIN_NS}}}row"),
        start=1,
    ):
        row_number = int(row.attrib.get("r", fallback_row))
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            reference = cell.attrib.get("r", "")
            column = _column_index(reference)
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                inline = cell.find(f"{{{MAIN_NS}}}is")
                value = (
                    "".join(
                        node.text or ""
                        for node in inline.iter(f"{{{MAIN_NS}}}t")
                    )
                    if inline is not None
                    else ""
                )
            else:
                value_node = cell.find(f"{{{MAIN_NS}}}v")
                value = value_node.text if value_node is not None else ""
                if cell_type == "s" and value:
                    index = int(value)
                    value = (
                        shared_strings[index]
                        if 0 <= index < len(shared_strings)
                        else ""
                    )
                elif cell_type == "b":
                    value = "TRUE" if value == "1" else "FALSE"
            values[column] = _cell_text(value)

        if values:
            width = max(values) + 1
            result.append(
                (
                    row_number,
                    tuple(values.get(column, "") for column in range(width)),
                )
            )
    return result


def _find_header(
    rows: list[tuple[int, tuple[str, ...]]],
) -> tuple[int, list[str], dict[int, dict[str, int]]] | None:
    """Find the first compatible header in the scan window."""
    for row_number, row in rows:
        if row_number > MAX_HEADER_SCAN_ROWS:
            break
        headers = [_cell_text(value) for value in row]
        if not all(header in headers for header in BASE_HEADERS):
            continue

        slot_fields = {
            "账号": "account",
            "练度": "training",
            "群成员昵称": "member_nickname",
            "备注": "note",
        }
        discovered_slots: dict[int, dict[str, int]] = {}
        for column, header in enumerate(headers):
            match = SLOT_HEADER.fullmatch(header)
            if not match:
                continue
            prefix, slot_text = match.groups()
            slot = int(slot_text)
            discovered_slots.setdefault(slot, {})[slot_fields[prefix]] = column

        slots = {
            slot: columns
            for slot, columns in discovered_slots.items()
            if "account" in columns
        }
        if slots:
            return row_number, headers, slots
    return None


def parse_workbook(content: bytes) -> WorkbookImport:
    """Parse a support workbook into normalized records.

    Args:
        content: Raw XLSX bytes.

    Returns:
        Parsed workbook data.

    Raises:
        ValueError: If the file is invalid or has no compatible worksheet.
    """
    try:
        archive = ZipFile(BytesIO(content))
        with archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise OverflowError
            strings = _shared_strings(archive)
            sheets = [
                (name, _worksheet_rows(archive, path, strings))
                for name, path in _sheet_definitions(archive)
            ]
    except OverflowError as exc:
        raise ValueError("工作簿解压后超过 100 MiB，已拒绝解析。") from exc
    except (
        BadZipFile,
        IndexError,
        KeyError,
        ET.ParseError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise ValueError("无法读取该文件，请确认它是有效的 .xlsx 工作簿。") from exc

    result = WorkbookImport()
    for sheet_name, rows in sheets:
        header_info = _find_header(rows)
        if header_info is None:
            if rows:
                result.warnings.append(
                    f"工作表“{sheet_name}”未找到兼容表头，已跳过。"
                )
            continue

        header_row, headers, slots = header_info
        rarity_column = headers.index("稀有度")
        profession_column = headers.index("职业")
        operator_column = headers.index("干员名")
        result.sheets.append(sheet_name)

        for source_row, row in rows:
            if source_row <= header_row:
                continue
            operator_name = _cell_text(
                row[operator_column] if operator_column < len(row) else None
            )
            if not operator_name:
                continue

            operator_index = len(result.operators)
            result.operators.append(
                OperatorRecord(
                    server=sheet_name,
                    rarity=_cell_text(
                        row[rarity_column] if rarity_column < len(row) else None
                    ),
                    profession=_cell_text(
                        row[profession_column]
                        if profession_column < len(row)
                        else None
                    ),
                    operator_name=operator_name,
                    normalized_name=normalize_operator_name(operator_name),
                    source_row=source_row,
                )
            )

            for slot, columns in sorted(slots.items()):
                account_column = columns["account"]
                account = _cell_text(
                    row[account_column] if account_column < len(row) else None
                )
                values: dict[str, str] = {}
                for field in ("training", "member_nickname", "note"):
                    column = columns.get(field)
                    values[field] = _cell_text(
                        row[column]
                        if column is not None and column < len(row)
                        else None
                    )

                if not account:
                    if any(values.values()):
                        result.warnings.append(
                            f"工作表“{sheet_name}”第 {source_row} 行账号{slot}"
                            "为空，其附属字段已忽略。"
                        )
                    continue

                result.supports.append(
                    SupportRecord(
                        operator_index=operator_index,
                        slot=slot,
                        account=account,
                        training=values["training"],
                        member_nickname=values["member_nickname"],
                        note=values["note"],
                    )
                )

    if not result.sheets:
        raise ValueError(
            "没有找到兼容的工作表。表头必须包含稀有度、职业、干员名和至少一个账号N。"
        )
    return result
