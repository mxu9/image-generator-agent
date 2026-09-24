import tempfile
import unittest
from pathlib import Path

from image_generator.image_llm import StubImageLLM
from image_generator.output_store import MIN_PNG, OutputStore
from image_generator.session import PromptSpec


class OutputStoreTests(unittest.TestCase):
    def test_min_png_header(self) -> None:
        self.assertTrue(MIN_PNG.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(MIN_PNG.endswith(b"IEND\xaeB`\x82"))

    def test_unique_run_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OutputStore(Path(tmp))
            first = store.make_run_dir()
            second = store.make_run_dir()
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertNotEqual(first, second)

    def test_stub_writes_png_and_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            llm = StubImageLLM(OutputStore(Path(tmp)))
            spec = PromptSpec(prompt="一只猫", negative_prompt="糊", width=576, height=1024)
            path = llm.generate(spec)
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "image.png")
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))
            run = path.parent
            self.assertIn("一只猫", (run / "prompt.txt").read_text(encoding="utf-8"))
            self.assertIn("糊", (run / "negative_prompt.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
