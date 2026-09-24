import base64
import tempfile
import unittest
from pathlib import Path

import httpx

from image_generator.config import ImageLLMConfig
from image_generator.image_llm import (
    ImageLLMError,
    SdImageLLM,
    StubImageLLM,
    build_image_llm,
)
from image_generator.output_store import OutputStore
from image_generator.session import PromptSpec

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-data"


def make_llm(handler, model: str = "m1") -> tuple[SdImageLLM, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    config = ImageLLMConfig(
        backend="sd",
        base_url="http://sd.test",
        api_key="",
        model=model,
        timeout=5.0,
        connect_timeout=5.0,
    )
    llm = SdImageLLM(config, OutputStore(Path(tempfile.mkdtemp())), transport=httpx.MockTransport(wrapped))
    llm.RETRY_DELAY = 0.0
    return llm, requests


def spec() -> PromptSpec:
    return PromptSpec(prompt="一只猫", negative_prompt="糊", width=576, height=1024)


class HealthTests(unittest.TestCase):
    def test_ok(self) -> None:
        llm, _ = make_llm(lambda request: httpx.Response(200, json={"data": [{"id": "m1"}]}))
        result = llm.health()
        self.assertTrue(result.ok)
        self.assertIn("m1", result.message)

    def test_model_missing(self) -> None:
        llm, _ = make_llm(lambda request: httpx.Response(200, json={"data": [{"id": "other"}]}))
        result = llm.health()
        self.assertFalse(result.ok)
        self.assertIn("不在服务列表中", result.message)

    def test_connect_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        llm, _ = make_llm(handler)
        result = llm.health()
        self.assertFalse(result.ok)
        self.assertIn("无法连接", result.message)


class GenerateTests(unittest.TestCase):
    def test_b64_saved(self) -> None:
        payload = {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}
        llm, requests = make_llm(lambda request: httpx.Response(200, json=payload))
        path = llm.generate(spec())
        self.assertEqual(path.read_bytes(), PNG_BYTES)
        run = path.parent
        self.assertEqual((run / "prompt.txt").read_text(encoding="utf-8").strip(), "一只猫")
        self.assertEqual((run / "negative_prompt.txt").read_text(encoding="utf-8").strip(), "糊")
        self.assertEqual(len(requests), 1)
        sent = requests[0].read()
        self.assertIn(b'"negative_prompt"', sent)
        self.assertIn(b'"width":576', sent)

    def test_url_fallback(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/images/generations"):
                return httpx.Response(200, json={"data": [{"url": "http://sd.test/files/img.png"}]})
            return httpx.Response(200, content=PNG_BYTES)

        llm, _ = make_llm(handler)
        path = llm.generate(spec())
        self.assertEqual(path.read_bytes(), PNG_BYTES)

    def test_http_4xx_no_retry(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(400, text="bad request")

        llm, _ = make_llm(handler)
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_HTTP_4XX)
        self.assertEqual(calls["n"], 1)

    def test_http_5xx_retries_then_success(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(500, text="server error")
            payload = {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}
            return httpx.Response(200, json=payload)

        llm, _ = make_llm(handler)
        path = llm.generate(spec())
        self.assertEqual(path.read_bytes(), PNG_BYTES)
        self.assertEqual(calls["n"], 3)

    def test_http_5xx_retries_exhausted(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503, text="unavailable")

        llm, _ = make_llm(handler)
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_HTTP_5XX)
        self.assertEqual(calls["n"], 3)

    def test_oom_not_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, text="CUDA out of memory")

        llm, _ = make_llm(handler)
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_OOM)
        self.assertEqual(calls["n"], 1)

    def test_connect_error_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectError("down")

        llm, _ = make_llm(handler)
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_CONNECT)
        self.assertEqual(calls["n"], 3)

    def test_bad_response_shape(self) -> None:
        llm, _ = make_llm(lambda request: httpx.Response(200, json={"data": []}))
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_BAD_RESPONSE)


class FactoryTests(unittest.TestCase):
    def test_sd_when_configured(self) -> None:
        store = OutputStore(Path(tempfile.mkdtemp()))
        config = ImageLLMConfig(backend="sd", base_url="http://sd.test")
        self.assertIsInstance(build_image_llm(config, store), SdImageLLM)

    def test_stub_when_no_url(self) -> None:
        store = OutputStore(Path(tempfile.mkdtemp()))
        config = ImageLLMConfig(backend="sd", base_url="")
        self.assertIsInstance(build_image_llm(config, store), StubImageLLM)

    def test_stub_backend(self) -> None:
        store = OutputStore(Path(tempfile.mkdtemp()))
        config = ImageLLMConfig(backend="stub", base_url="http://sd.test")
        self.assertIsInstance(build_image_llm(config, store), StubImageLLM)


if __name__ == "__main__":
    unittest.main()
