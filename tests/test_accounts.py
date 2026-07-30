from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from arksupport.accounts import AccountStore
from arksupport.auth import (
    generate_token,
    hash_password,
    normalize_username,
    token_digest,
    validate_password,
    verify_password,
)
from arksupport.storage import SupportStore


class AuthHelpersTest(unittest.TestCase):
    def test_hashes_and_verifies_password(self) -> None:
        encoded = hash_password("correct horse battery")
        self.assertTrue(verify_password("correct horse battery", encoded))
        self.assertFalse(verify_password("wrong password", encoded))
        self.assertNotIn("correct horse battery", encoded)

    def test_validates_username_and_password_policy(self) -> None:
        self.assertEqual(normalize_username("Test.User"), "test.user")
        with self.assertRaises(ValueError):
            normalize_username("中文用户")
        with self.assertRaises(ValueError):
            validate_password("short")


class AccountStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "arksupport.sqlite3"
        self.support_store = SupportStore(self.database_path)
        self.support_store.initialize()
        self.accounts = AccountStore(self.database_path)
        self.accounts.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_user(self, username: str, role: str = "user") -> dict:
        return self.accounts.create_user(
            username=username,
            password_hash=hash_password("temporary password"),
            role=role,
        )

    def test_invite_is_single_use_and_case_insensitive_usernames(self) -> None:
        code = generate_token()
        self.accounts.create_invite(
            invite_id=uuid.uuid4().hex,
            code_hash=token_digest(code),
            creator_user_id=None,
        )
        user = self.accounts.register_with_invite(
            invite_hash=token_digest(code),
            username="Example.User",
            password_hash=hash_password("registered password"),
        )
        self.assertEqual(user["role"], "user")

        with self.assertRaisesRegex(ValueError, "邀请码"):
            self.accounts.register_with_invite(
                invite_hash=token_digest(code),
                username="Other.User",
                password_hash=hash_password("registered password"),
            )
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.accounts.create_user(
                username="example.user",
                password_hash=hash_password("another password"),
            )

    def test_revoked_invite_cannot_be_used(self) -> None:
        code = generate_token()
        invite_id = uuid.uuid4().hex
        self.accounts.create_invite(
            invite_id=invite_id,
            code_hash=token_digest(code),
            creator_user_id=None,
        )
        self.assertTrue(
            self.accounts.revoke_invite(invite_id, allow_all=True)
        )
        with self.assertRaisesRegex(ValueError, "邀请码"):
            self.accounts.register_with_invite(
                invite_hash=token_digest(code),
                username="new-user",
                password_hash=hash_password("registered password"),
            )

    def test_disabled_user_session_is_revoked(self) -> None:
        user = self.create_user("normal-user")
        raw_token = generate_token()
        self.accounts.create_session(
            user_id=user["id"],
            token_hash=token_digest(raw_token),
            csrf_token="csrf",
        )
        self.assertIsNotNone(
            self.accounts.get_session(token_digest(raw_token))
        )

        self.accounts.set_user_active(
            user["id"],
            False,
            allow_admin_target=False,
        )
        self.assertIsNone(self.accounts.get_session(token_digest(raw_token)))

    def test_admin_target_is_protected_from_regular_admin_operations(self) -> None:
        admin = self.create_user("admin-user", role="admin")
        self.assertIsNone(
            self.accounts.set_user_active(
                admin["id"],
                False,
                allow_admin_target=False,
            )
        )
        self.assertIsNone(
            self.accounts.set_password(
                admin["id"],
                hash_password("replacement password"),
                must_change_password=True,
                allow_admin_target=False,
            )
        )

    def test_group_workbooks_are_shared_but_remarks_are_private(self) -> None:
        first = self.create_user("first-user")
        second = self.create_user("second-user")
        admin = self.create_user("admin-user", role="admin")
        group = self.support_store.ensure_manual_group(
            "bot:GroupMessage:10001"
        )
        self.accounts.link_group(
            user_id=first["id"],
            binding_id=group["id"],
            remark="第一人的私有备注",
        )
        self.accounts.link_group(
            user_id=second["id"],
            binding_id=group["id"],
            remark="第二人的私有备注",
        )

        first_groups = self.accounts.list_visible_groups(
            user_id=first["id"],
            role="user",
        )
        second_groups = self.accounts.list_visible_groups(
            user_id=second["id"],
            role="user",
        )
        admin_groups = self.accounts.list_visible_groups(
            user_id=admin["id"],
            role="admin",
        )

        self.assertEqual(first_groups[0]["remark"], "第一人的私有备注")
        self.assertEqual(second_groups[0]["remark"], "第二人的私有备注")
        self.assertEqual(admin_groups[0]["remark"], "")
        self.assertTrue(
            self.accounts.can_access_group(
                user_id=first["id"],
                role="user",
                binding_id=group["id"],
            )
        )
        self.assertTrue(
            self.accounts.can_access_group(
                user_id=admin["id"],
                role="admin",
                binding_id=group["id"],
            )
        )

    def test_unlink_preserves_global_group(self) -> None:
        user = self.create_user("normal-user")
        group = self.support_store.ensure_manual_group(
            "bot:GroupMessage:10001"
        )
        self.accounts.link_group(
            user_id=user["id"],
            binding_id=group["id"],
            remark="remark",
        )
        self.assertTrue(
            self.accounts.unlink_group(
                user_id=user["id"],
                binding_id=group["id"],
            )
        )
        self.assertIsNotNone(
            self.support_store.get_group_by_umo(group["umo"])
        )

    def test_account_schema_upgrade_preserves_existing_groups(self) -> None:
        other_database = Path(self.temp.name) / "legacy.sqlite3"
        support_store = SupportStore(other_database)
        support_store.initialize()
        group = support_store.ensure_manual_group(
            "bot:GroupMessage:legacy-group"
        )

        accounts = AccountStore(other_database)
        accounts.initialize()

        self.assertEqual(
            support_store.get_group_by_umo(group["umo"])["id"],
            group["id"],
        )

    def test_regular_admin_lists_only_regular_accounts(self) -> None:
        self.create_user("normal-user")
        self.create_user("admin-user", role="admin")
        regular_users = self.accounts.list_users(include_admins=False)
        all_users = self.accounts.list_users(include_admins=True)
        self.assertEqual([user["username"] for user in regular_users], ["normal-user"])
        self.assertEqual(len(all_users), 2)


if __name__ == "__main__":
    unittest.main()
