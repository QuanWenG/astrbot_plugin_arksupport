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
                        "群成员昵称1",
                        "备注1",
                        "账号2",
                        "练度2",
                        "群成员昵称2",
                        "备注2",
                    ],
                    [
                        "6★", "先锋", "风笛",
                        "玩家甲#1234", "全满", "甲昵称", "",
                        None, "", "", "",
                    ],
                    [
                        "6★", "近卫", "维娜 维多利亚",
                        None, "", "", "",
                        "玩家乙", "精二", "乙昵称", "二技能",
                    ],
                    [
                        "6★", "术士", "艾雅法拉",
                        None, "", "", "",
                        None, "", "", "",
                    ],
                ],
                "B服": [
                    [
                        "稀有度",
                        "职业",
                        "干员名",
                        "账号1",
                        "练度1",
                        "群成员昵称1",
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
        self.assertEqual(result.supports[2].member_nickname, "测试群")
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("说明", result.warnings[0])

    def test_groups_arbitrary_numbered_slots_by_header_suffix(self) -> None:
        content = make_xlsx(
            {
                "官服": [
                    [
                        "稀有度",
                        "职业",
                        "干员名",
                        "备注4",
                        "群成员昵称4",
                        "练度4",
                        "账号4",
                        "账号12",
                        "群成员昵称12",
                        "备注12",
                        "练度12",
                    ],
                    [
                        "6★",
                        "先锋",
                        "风笛",
                        "四号备注",
                        "四号昵称",
                        "四号练度",
                        "四号账号",
                        "十二号账号",
                        "十二号昵称",
                        "十二号备注",
                        "十二号练度",
                    ],
                ]
            }
        )

        result = parse_workbook(content)

        self.assertEqual([item.slot for item in result.supports], [4, 12])
        self.assertEqual(result.supports[0].member_nickname, "四号昵称")
        self.assertEqual(result.supports[1].training, "十二号练度")

    def test_does_not_treat_legacy_group_name_as_member_nickname(self) -> None:
        content = make_xlsx(
            {
                "官服": [
                    [
                        "稀有度",
                        "职业",
                        "干员名",
                        "账号1",
                        "练度1",
                        "群名称1",
                        "备注1",
                    ],
                    ["6★", "先锋", "风笛", "玩家甲", "全满", "旧群名", ""],
                ]
            }
        )

        result = parse_workbook(content)

        self.assertEqual(result.supports[0].member_nickname, "")

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
