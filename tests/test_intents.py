import unittest

from image_generator.intents import (
    Intent,
    allowed_intents,
    build_clarification_question,
    parse_numbered_choice,
)


class IntentTests(unittest.TestCase):
    def test_idle_without_spec(self) -> None:
        self.assertEqual(
            allowed_intents("idle", False),
            [Intent.NEW_GENERATE, Intent.NEW_PREVIEW],
        )

    def test_idle_with_spec(self) -> None:
        self.assertEqual(
            allowed_intents("idle", True),
            [
                Intent.NEW_GENERATE,
                Intent.NEW_PREVIEW,
                Intent.PATCH_AND_GENERATE,
                Intent.PATCH_AND_PREVIEW,
            ],
        )

    def test_preview(self) -> None:
        self.assertEqual(
            allowed_intents("preview", True),
            [
                Intent.CONFIRM_GENERATE,
                Intent.PATCH_AND_GENERATE,
                Intent.PATCH_AND_PREVIEW,
                Intent.CANCEL_GENERATE,
            ],
        )

    def test_parse_plain_number(self) -> None:
        self.assertEqual(parse_numbered_choice("2", 4), (2, ""))

    def test_parse_number_with_instruction(self) -> None:
        self.assertEqual(parse_numbered_choice("2 光线再暗一点", 4), (2, "光线再暗一点"))

    def test_parse_dotted_and_select(self) -> None:
        self.assertEqual(parse_numbered_choice("3、", 4), (3, ""))
        self.assertEqual(parse_numbered_choice("选4", 4), (4, ""))

    def test_parse_out_of_range(self) -> None:
        self.assertIsNone(parse_numbered_choice("5", 4))
        self.assertIsNone(parse_numbered_choice("hello", 4))

    def test_question_is_numbered(self) -> None:
        question = build_clarification_question(allowed_intents("preview", True))
        self.assertIn("1. 用当前提示词生图", question)
        self.assertIn("4. 取消这次生图", question)


if __name__ == "__main__":
    unittest.main()
