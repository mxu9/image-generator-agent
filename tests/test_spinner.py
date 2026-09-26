import time
import unittest

from image_generator.spinner import ASCII_FRAMES, CIRCLE_FRAMES, Spinner


class MemoryStream:
    def __init__(self, tty: bool, encoding: str = "utf-8") -> None:
        self._tty = tty
        self.encoding = encoding
        self.parts: list[str] = []

    def write(self, text: str) -> int:
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return self._tty

    def getvalue(self) -> str:
        return "".join(self.parts)


class SpinnerTests(unittest.TestCase):
    def test_non_tty_is_silent(self) -> None:
        stream = MemoryStream(tty=False)
        with Spinner("正在生图", stream=stream, interval=0.01):
            time.sleep(0.03)
        self.assertEqual(stream.getvalue(), "")

    def test_tty_shows_circle_then_clears(self) -> None:
        stream = MemoryStream(tty=True)
        with Spinner("正在生图", stream=stream, interval=0.01):
            time.sleep(0.03)
        text = stream.getvalue()
        self.assertIn("正在生图", text)
        self.assertIn("s", text)
        self.assertTrue(any(frame in text for frame in CIRCLE_FRAMES))
        self.assertTrue(text.endswith("\r"))
        self.assertNotIn("正在生图", text.split("\r")[-1])

    def test_ascii_fallback(self) -> None:
        stream = MemoryStream(tty=True, encoding="ascii")
        with Spinner("wait", stream=stream, interval=0.01):
            time.sleep(0.03)
        text = stream.getvalue()
        self.assertTrue(any(frame in text for frame in ASCII_FRAMES))
        self.assertNotIn(CIRCLE_FRAMES[0], text)

    def test_exception_clears_and_stops(self) -> None:
        stream = MemoryStream(tty=True)
        with self.assertRaises(RuntimeError):
            with Spinner("正在生图", stream=stream, interval=0.01):
                time.sleep(0.02)
                raise RuntimeError("boom")
        text = stream.getvalue()
        self.assertIn("正在生图", text)
        self.assertTrue(text.endswith("\r"))
        written = len(text)
        time.sleep(0.05)
        self.assertEqual(len(stream.getvalue()), written)


if __name__ == "__main__":
    unittest.main()
