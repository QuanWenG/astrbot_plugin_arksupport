from __future__ import annotations

import unittest

from arksupport.parser import normalize_operator_name, parse_workbook
from tests.xlsx_factory import make_xlsx


class WorkbookParserTest(unittest.TestCase):
    def test_parses_numbered_slots_and_optional_fields(self) -> None:
        content = make_xlsx(
            {
                "说明": [["本页不导入"]],
                "官服": [
                    ["标题"],
                    [
                        "稀有度",
                        "职业",
                        "干员名",
                        "账号1",
                        "练度1",
                        "备注1",
                        "账号2",
                        "练度2",
                        "备注2",
                    ],
                    ["6★", "先锋", "风笛", "玩家甲#1234", "全满", "", None, "", ""],
                    ["6★", "近卫", "维娜 维多利亚", None, "", "", "玩家乙", "精二", "二技能"],
                    ["6★", "术士", "艾雅法拉", None, "", "", None, "", ""],
                ],
                "B服": [
                    [
                        "稀有度",
                        "职业",
                        "干员名",
                        "账号1",
                        "练度1",
                        "群名称1",
                        "备注1",
                    ],
                    ["6★", "先锋", "风笛", "玩家丙", "全满", "测试群", "满模组"],
                ],
            }
        )

        result = parse_workbook(content)

        self.assertEqual(result.sheets, ["官服", "B服"])
        self.assertEqual(len(result.operators), 4)
        self.assertEqual(len(result.supports), 3)
        self.assertEqual(result.supports[1].slot, 2)
        self.assertEqual(result.supports[2].group_name, "测试群")
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("说明", result.warnings[0])

    def test_warns_about_orphan_slot_fields(self) -> None:
        content = make_xlsx(
            {
                "官服": [
                    ["稀有度", "职业", "干员名", "账号1", "练度1"],
                    ["6★", "先锋", "风笛", None, "全满"],
                ]
            }
        )

        result = parse_workbook(content)

        self.assertEqual(len(result.operators), 1)
        self.assertEqual(len(result.supports), 0)
        self.assertEqual(len(result.warnings), 1)

    def test_rejects_workbook_without_compatible_sheet(self) -> None:
        content = make_xlsx({"说明": [["没有标准表头"]]})
        with self.assertRaisesRegex(ValueError, "没有找到兼容"):
            parse_workbook(content)

    def test_normalization_ignores_width_case_and_whitespace(self) -> None:
        self.assertEqual(
            normalize_operator_name(" ＣＯＮＦＥＳＳ - 47 "),
            normalize_operator_name("confess-47"),
        )
        self.assertEqual(
            normalize_operator_name("维娜 维多利亚"),
            normalize_operator_name("维娜维多利亚"),
        )


if __name__ == "__main__":
    unittest.main()

