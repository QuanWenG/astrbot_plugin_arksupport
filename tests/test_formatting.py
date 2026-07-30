import unittest

from arksupport.formatting import (
    append_data_update_footer,
    format_support_entry,
)


class SupportFormattingTest(unittest.TestCase):
    def test_formats_complete_entry(self) -> None:
        self.assertEqual(
            format_support_entry(
                {
                    "account": "示例#0001",
                    "training": "满专满潜 非满模组",
                    "member_nickname": "群成员昵称",
                    "note": "3级X模",
                }
            ),
            "示例#0001｜练度：满专满潜 非满模组"
            "｜群id：群成员昵称｜备注：3级X模",
        )

    def test_omits_empty_optional_fields(self) -> None:
        self.assertEqual(
            format_support_entry(
                {
                    "account": "示例#0001",
                    "training": "",
                    "member_nickname": "",
                    "note": "仅备注",
                }
            ),
            "示例#0001｜备注：仅备注",
        )

    def test_keeps_nickname_when_training_is_empty(self) -> None:
        self.assertEqual(
            format_support_entry(
                {
                    "account": "示例#0001",
                    "training": "",
                    "member_nickname": "群成员昵称",
                    "note": "",
                }
            ),
            "示例#0001｜群id：群成员昵称",
        )

    def test_omits_nickname_without_dropping_other_fields(self) -> None:
        self.assertEqual(
            format_support_entry(
                {
                    "account": "示例#0001",
                    "training": "全满",
                    "member_nickname": "",
                    "note": "3级X模",
                }
            ),
            "示例#0001｜练度：全满｜备注：3级X模",
        )

    def test_appends_update_date_without_time(self) -> None:
        self.assertEqual(
            append_data_update_footer(
                "查询结果",
                "2026-07-29T12:34:56+00:00",
            ),
            "查询结果\n\n数据最后更新时间：2026-07-29",
        )

    def test_does_not_append_footer_without_import_time(self) -> None:
        self.assertEqual(
            append_data_update_footer("查询结果", None),
            "查询结果",
        )


if __name__ == "__main__":
    unittest.main()
