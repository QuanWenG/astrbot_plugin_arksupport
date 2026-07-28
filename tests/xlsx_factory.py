from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def make_xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    """Build a minimal XLSX fixture using inline strings."""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
            </Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
            </Relationships>""",
        )

        sheet_nodes = []
        relation_nodes = []
        for number, (sheet_name, rows) in enumerate(sheets.items(), start=1):
            sheet_nodes.append(
                f'<sheet name="{escape(sheet_name)}" sheetId="{number}" '
                f'r:id="rId{number}"/>'
            )
            relation_nodes.append(
                f'<Relationship Id="rId{number}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{number}.xml"/>'
            )

            row_nodes = []
            for row_number, row in enumerate(rows, start=1):
                cell_nodes = []
                for column, value in enumerate(row, start=1):
                    if value is None:
                        continue
                    reference = f"{_column_name(column)}{row_number}"
                    cell_nodes.append(
                        f'<c r="{reference}" t="inlineStr"><is><t>'
                        f"{escape(str(value))}</t></is></c>"
                    )
                row_nodes.append(
                    f'<row r="{row_number}">{"".join(cell_nodes)}</row>'
                )
            archive.writestr(
                f"xl/worksheets/sheet{number}.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>"""
                + "".join(row_nodes)
                + "</sheetData></worksheet>",
            )

        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets>"""
            + "".join(sheet_nodes)
            + "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">"""
            + "".join(relation_nodes)
            + "</Relationships>",
        )
    return output.getvalue()

