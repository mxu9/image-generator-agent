import unittest

from image_generator.smart_llm import SmartLLMError, spec_from_payload


class SpecParseTests(unittest.TestCase):
    def test_ok(self) -> None:
        spec = spec_from_payload(
            {"prompt": "一只猫", "negative_prompt": "卡通", "width": 576, "height": 1024}
        )
        self.assertEqual(spec.prompt, "一只猫")
        self.assertEqual(spec.width, 576)

    def test_empty_prompt(self) -> None:
        with self.assertRaises(SmartLLMError):
            spec_from_payload({"prompt": "  ", "width": 1, "height": 1})

    def test_default_size(self) -> None:
        spec = spec_from_payload({"prompt": "花"})
        self.assertEqual(spec.width, 576)
        self.assertEqual(spec.height, 1024)


if __name__ == "__main__":
    unittest.main()
