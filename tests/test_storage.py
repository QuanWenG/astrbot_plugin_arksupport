from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from re import fullmatch

from arksupport.parser import (
    SupportRecord,
    WorkbookImport,
    normalize_operator_name,
    parse_workbook,
)
from arksupport.storage import (
    SupportStore,
    parse_group_umo,
    validate_group_remark,
)
from tests.xlsx_factory import make_xlsx


def parsed(account: str) -> WorkbookImport:
    return parse_workbook(
        make_xlsx(
            {
                "官服": [
                    [
                        "稀有度",
                        "职业",
                        "干员名",
                        "账号1",
                        "练度1",
                        "群成员昵称1",
                        "备注1",
                    ],
                    [
                        "6★",
                        "先锋",
                        "风笛",
                        account,
                        "全满",
                        f"{account}昵称",
                        "测试",
                    ],
                    [
                        "6★",
                        "术士",
                        "艾雅法拉",
                        None,
                        None,
                        None,
                        None,
                    ],
                ]
            }
        )
    )


class SupportStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = SupportStore(
            Path(self.temporary_directory.name) / "arksupport.sqlite3"
        )
        self.store.initialize()
        self.first_group = self.store.register_group(
            umo="bot-a:GroupMessage:10001",
            platform_id="bot-a",
            group_id="10001",
            group_name="第一群",
        )
        self.second_group = self.store.register_group(
            umo="bot-a:GroupMessage:10002",
            platform_id="bot-a",
            group_id="10002",
            group_name="第二群",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_group_data_is_isolated(self) -> None:
        self.store.import_workbook(
            binding_id=self.first_group["id"],
            filename="first.xlsx",
            sha256="a",
            imported=parsed("玩家甲"),
        )
        self.store.import_workbook(
            binding_id=self.second_group["id"],
            filename="second.xlsx",
            sha256="b",
            imported=parsed("玩家乙"),
        )

        first = self.store.query_operator(
            self.first_group["umo"],
            normalize_operator_name("风笛"),
        )
        second = self.store.query_operator(
            self.second_group["umo"],
            normalize_operator_name("风笛"),
        )

        self.assertEqual([item["account"] for item in first["entries"]], ["玩家甲"])
        self.assertEqual([item["account"] for item in second["entries"]], ["玩家乙"])
        self.assertEqual(first["entries"][0]["member_nickname"], "玩家甲昵称")
        self.assertIsNotNone(
            fullmatch(
                r"\d{4}-\d{2}-\d{2}T.+",
                first["last_updated_at"],
            )
        )

    def test_manual_group_remark_survives_chat_registration(self) -> None:
        manual = self.store.add_manual_group(
            umo="bot-b:GroupMessage:20001",
            remark="手动添加的测试群",
        )
        self.assertEqual(manual["remark"], "手动添加的测试群")
        self.assertEqual(manual["platform_id"], "bot-b")
        self.assertEqual(manual["group_id"], "20001")

        refreshed = self.store.register_group(
            umo="bot-b:GroupMessage:20001",
            platform_id="bot-b",
            group_id="20001",
            group_name="平台识别群名",
        )
        self.assertEqual(refreshed["group_name"], "平台识别群名")
        self.assertEqual(refreshed["remark"], "手动添加的测试群")

        updated = self.store.update_group_remark(
            manual["id"],
            "新的备注",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["remark"], "新的备注")

    def test_known_operator_without_support_is_distinct_from_unknown(self) -> None:
        self.store.import_workbook(
            binding_id=self.first_group["id"],
            filename="first.xlsx",
            sha256="a",
            imported=parsed("玩家甲"),
        )

        known = self.store.query_operator(
            self.first_group["umo"],
            normalize_operator_name("艾雅法拉"),
        )
        unknown = self.store.query_operator(
            self.first_group["umo"],
            normalize_operator_name("不存在"),
        )

        self.assertEqual(known["matched_names"], ["艾雅法拉"])
        self.assertEqual(known["entries"], [])
        self.assertEqual(unknown["matched_names"], [])

    def test_replaces_workbook_transactionally(self) -> None:
        original = self.store.import_workbook(
            binding_id=self.first_group["id"],
            filename="first.xlsx",
            sha256="a",
            imported=parsed("玩家甲"),
        )
        self.store.import_workbook(
            binding_id=self.first_group["id"],
            filename="replacement.xlsx",
            sha256="b",
            imported=parsed("玩家乙"),
            workbook_id=original["id"],
        )

        workbooks = self.store.list_workbooks(self.first_group["id"])
        result = self.store.query_operator(
            self.first_group["umo"],
            normalize_operator_name("风笛"),
        )

        self.assertEqual(len(workbooks), 1)
        self.assertEqual(workbooks[0]["original_filename"], "replacement.xlsx")
        self.assertEqual([item["account"] for item in result["entries"]], ["玩家乙"])

    def test_failed_replacement_rolls_back_old_data(self) -> None:
        original = self.store.import_workbook(
            binding_id=self.first_group["id"],
            filename="first.xlsx",
            sha256="a",
            imported=parsed("玩家甲"),
        )
        invalid = parsed("玩家乙")
        invalid.supports.append(
            SupportRecord(
                operator_index=999,
                slot=2,
                account="损坏记录",
                training="",
                member_nickname="",
                note="",
            )
        )

        with self.assertRaises(IndexError):
            self.store.import_workbook(
                binding_id=self.first_group["id"],
                filename="broken.xlsx",
                sha256="b",
                imported=invalid,
                workbook_id=original["id"],
            )

        workbooks = self.store.list_workbooks(self.first_group["id"])
        result = self.store.query_operator(
            self.first_group["umo"],
            normalize_operator_name("风笛"),
        )
        self.assertEqual(workbooks[0]["original_filename"], "first.xlsx")
        self.assertEqual([item["account"] for item in result["entries"]], ["玩家甲"])

    def test_deleting_group_cascades_workbooks(self) -> None:
        self.store.import_workbook(
            binding_id=self.first_group["id"],
            filename="first.xlsx",
            sha256="a",
            imported=parsed("玩家甲"),
        )

        self.assertTrue(self.store.delete_group(self.first_group["id"]))
        self.assertEqual(self.store.list_workbooks(self.first_group["id"]), [])
        result = self.store.query_operator(
            self.first_group["umo"],
            normalize_operator_name("风笛"),
        )
        self.assertFalse(result["registered"])


class GroupBindingValidationTest(unittest.TestCase):
    def test_accepts_group_umo_with_colons_in_session_id(self) -> None:
        self.assertEqual(
            parse_group_umo("bot-a:GroupMessage:room:subroom"),
            ("bot-a", "room:subroom"),
        )

    def test_rejects_private_or_malformed_umo(self) -> None:
        with self.assertRaisesRegex(ValueError, "GroupMessage"):
            parse_group_umo("bot-a:FriendMessage:10001")
        with self.assertRaisesRegex(ValueError, "格式"):
            parse_group_umo("not-an-umo")

    def test_rejects_oversized_remark(self) -> None:
        with self.assertRaisesRegex(ValueError, "200"):
            validate_group_remark("测" * 201)

    def test_initialize_migrates_legacy_group_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE group_bindings (
                    id TEXT PRIMARY KEY,
                    umo TEXT NOT NULL UNIQUE,
                    platform_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
            connection.close()

            store = SupportStore(database_path)
            store.initialize()

            check = sqlite3.connect(database_path)
            columns = {
                row[1]
                for row in check.execute(
                    "PRAGMA table_info(group_bindings)"
                ).fetchall()
            }
            check.close()
            self.assertIn("remark", columns)

    def test_initialize_adds_member_nickname_to_legacy_supports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy-supports.sqlite3"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE supports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operator_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    account TEXT NOT NULL,
                    training TEXT NOT NULL,
                    source_group_name TEXT NOT NULL,
                    note TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO supports (
                    operator_id, slot, account, training,
                    source_group_name, note
                ) VALUES (1, 1, '旧账号', '全满', '旧群名', '旧备注')
                """
            )
            connection.commit()
            connection.close()

            store = SupportStore(database_path)
            store.initialize()

            check = sqlite3.connect(database_path)
            row = check.execute(
                "SELECT account, member_nickname FROM supports"
            ).fetchone()
            check.close()
            self.assertEqual(row, ("旧账号", ""))


if __name__ == "__main__":
    unittest.main()
