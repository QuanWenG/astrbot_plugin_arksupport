import json
import unittest

from arksupport.llm_matcher import (
    build_operator_match_prompt,
    parse_operator_match,
)


CATALOG = [
    {"operator_name": "塞雷娅", "normalized_name": "塞雷娅"},
    {"operator_name": "维娜·维多利亚", "normalized_name": "维娜·维多利亚"},
]


class LlmMatcherTests(unittest.TestCase):
    def test_build_prompt_contains_input_and_catalog(self):
        prompt = build_operator_match_prompt("塞妈", ["塞雷娅", "风笛"])
        self.assertIn('"塞妈"', prompt)
        self.assertIn('"塞雷娅"', prompt)
        self.assertIn('{"match":null}', prompt)

    def test_parse_valid_catalog_match(self):
        result = parse_operator_match(
            json.dumps({"match": "塞雷娅"}, ensure_ascii=False),
            CATALOG,
        )
        self.assertEqual(result, ("塞雷娅", "塞雷娅"))

    def test_parse_fenced_json(self):
        result = parse_operator_match(
            '```json\n{"match":"维娜·维多利亚"}\n```',
            CATALOG,
        )
        self.assertEqual(result, ("维娜·维多利亚", "维娜·维多利亚"))

    def test_rejects_name_outside_group_catalog(self):
        result = parse_operator_match('{"match":"风笛"}', CATALOG)
        self.assertIsNone(result)

    def test_accepts_null_as_no_match(self):
        self.assertIsNone(parse_operator_match('{"match":null}', CATALOG))

    def test_rejects_non_json_explanation(self):
        self.assertIsNone(
            parse_operator_match('我认为是塞雷娅。', CATALOG)
        )


if __name__ == "__main__":
    unittest.main()
