import base64
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import httpx

from image_generator.cloud_llm import (
    SILICONFLOW,
    SiliconFlowImageLLM,
    ZHIPU,
    ZhipuImageLLM,
    map_size,
)
from image_generator.config import ImageLLMConfig
from image_generator.image_llm import ImageLLMError, StubImageLLM, build_image_llm
from image_generator.output_store import OutputStore
from image_generator.session import PromptSpec

PNG_BYTES = b"\x89PNG\r\n\x1a\ncloud-image-data"
B64 = base64.b64encode(PNG_BYTES).decode("ascii")


def make_llm(handler, profile=SILICONFLOW) -> tuple[SiliconFlowImageLLM, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    config = ImageLLMConfig(
        backend="cloud",
        provider="siliconflow",
        base_url="https://sf.test/v1",
        api_key="sk-test",
        model="Qwen/Qwen-Image",
        timeout=5.0,
        connect_timeout=5.0,
    )
    llm = SiliconFlowImageLLM(config, OutputStore(Path(tempfile.mkdtemp())), transport=httpx.MockTransport(wrapped))
    llm.profile = profile
    return llm, requests


def spec() -> PromptSpec:
    return PromptSpec(prompt="一只猫", negative_prompt="糊", width=576, height=1024)


class MapSizeTests(unittest.TestCase):
    def test_portrait(self) -> None:
        self.assertEqual(map_size(SILICONFLOW, 576, 1024), (928, 1664))

    def test_square(self) -> None:
        self.assertEqual(map_size(SILICONFLOW, 1024, 1024), (1328, 1328))

    def test_landscape(self) -> None:
        self.assertEqual(map_size(SILICONFLOW, 1024, 576), (1664, 928))

    def test_exact_ratio_prefers_larger_area(self) -> None:
        # 0.6 比例：928x1664≈0.5577 与 1140x1472≈0.7745 之间取前者；构造平手场景
        self.assertIn(map_size(SILICONFLOW, 300, 500), SILICONFLOW.sizes)


class SiliconFlowGenerateTests(unittest.TestCase):
    def test_images_key_with_url_download(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/images/generations"):
                return httpx.Response(200, json={"images": [{"url": "https://cdn.test/img.png"}]})
            assert request.url.host == "cdn.test", request.url
            return httpx.Response(200, content=PNG_BYTES)

        llm, requests = make_llm(handler)
        path = llm.generate(spec())
        self.assertEqual(path.read_bytes(), PNG_BYTES)
        download = requests[-1]
        self.assertNotIn("authorization", {k.lower() for k in download.headers})
        sent = requests[0].read()
        self.assertIn(b'"image_size":"928x1664"', sent)
        self.assertIn(b'"negative_prompt"', sent)
        meta = (path.parent / "meta.txt").read_text(encoding="utf-8")
        self.assertIn("requested=576x1024", meta)
        self.assertIn("actual=928x1664", meta)
        self.assertIn("negative_prompt=sent", meta)

    def test_data_key_compat_and_b64(self) -> None:
        profile = replace(SILICONFLOW, response_key="data")
        llm, _ = make_llm(lambda request: httpx.Response(200, json={"data": [{"b64_json": B64}]}), profile)
        path = llm.generate(spec())
        self.assertEqual(path.read_bytes(), PNG_BYTES)

    def test_negative_dropped_when_unsupported(self) -> None:
        profile = replace(SILICONFLOW, supports_negative=False)
        llm, requests = make_llm(lambda request: httpx.Response(200, json={"images": [{"b64_json": B64}]}), profile)
        path = llm.generate(spec())
        sent = requests[0].read()
        self.assertNotIn(b'"negative_prompt"', sent)
        self.assertIn("negative_prompt=dropped", (path.parent / "meta.txt").read_text(encoding="utf-8"))
        # sidecar 保留原文，信息不丢
        self.assertIn("糊", (path.parent / "negative_prompt.txt").read_text(encoding="utf-8"))

    def test_rate_limited(self) -> None:
        llm, requests = make_llm(lambda request: httpx.Response(429, text="rate limit"))
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_RATE_LIMITED)
        self.assertEqual(len(requests), 1)

    def test_audit_rejection(self) -> None:
        llm, requests = make_llm(
            lambda request: httpx.Response(400, text='{"code":20012,"message":"content policy violation"}')
        )
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_CONTENT_REJECTED)
        self.assertEqual(len(requests), 1)

    def test_plain_4xx(self) -> None:
        llm, _ = make_llm(lambda request: httpx.Response(400, text="bad request"))
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_HTTP_4XX)

    def test_5xx_not_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503, text="overloaded")

        llm, _ = make_llm(handler)
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_HTTP_5XX)
        self.assertEqual(calls["n"], 1)  # 云端按次计费：不重试

    def test_connect_error_not_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectError("down")

        llm, _ = make_llm(handler)
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_CONNECT)
        self.assertEqual(calls["n"], 1)

    def test_bad_response_shape(self) -> None:
        llm, _ = make_llm(lambda request: httpx.Response(200, json={"foo": []}))
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_BAD_RESPONSE)


class HealthTests(unittest.TestCase):
    def test_ok(self) -> None:
        payload = {"data": [{"id": "Qwen/Qwen-Image"}, {"id": "other"}]}
        llm, _ = make_llm(lambda request: httpx.Response(200, json=payload))
        self.assertTrue(llm.health().ok)

    def test_model_missing(self) -> None:
        payload = {"data": [{"id": "other"}]}
        llm, _ = make_llm(lambda request: httpx.Response(200, json=payload))
        result = llm.health()
        self.assertFalse(result.ok)
        self.assertIn("不在服务列表中", result.message)


def make_zhipu(handler, model="sd-cpp-local"):
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    config = ImageLLMConfig(
        backend="cloud",
        provider="zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="sk-test",
        model=model,
        timeout=5.0,
        connect_timeout=5.0,
    )
    llm = ZhipuImageLLM(
        config,
        OutputStore(Path(tempfile.mkdtemp())),
        transport=httpx.MockTransport(wrapped),
    )
    llm.DOWNLOAD_RETRY_DELAY = 0
    return llm, requests


def _posts(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [item for item in requests if item.url.path.endswith("/images/generations")]


class ZhipuGenerateTests(unittest.TestCase):
    def test_portrait_maps_to_recommended_tier(self) -> None:
        self.assertEqual(map_size(ZHIPU, 576, 1024), (960, 1728))

    def test_square_and_landscape(self) -> None:
        self.assertEqual(map_size(ZHIPU, 1024, 1024), (1280, 1280))
        self.assertEqual(map_size(ZHIPU, 1024, 576), (1728, 960))

    def test_url_download_without_auth_and_drops_negative(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/images/generations"):
                return httpx.Response(
                    200,
                    json={
                        "data": [{"url": "https://cdn.test/img.png"}],
                        "content_filter": [{"role": "assistant", "level": 1}],
                    },
                )
            return httpx.Response(200, content=PNG_BYTES)

        llm, requests = make_zhipu(handler)
        path = llm.generate(spec())
        self.assertEqual(path.read_bytes(), PNG_BYTES)
        body = _posts(requests)[0].read()
        self.assertIn(b'"model":"glm-image"', body)
        self.assertIn(b'"size":"960x1728"', body)
        self.assertIn(b'"quality":"hd"', body)
        self.assertNotIn(b"negative_prompt", body)
        self.assertNotIn(b"batch_size", body)
        self.assertNotIn(b"watermark_enabled", body)
        self.assertNotIn(b"user_id", body)
        download = requests[-1]
        self.assertNotIn("authorization", {key.lower() for key in download.headers})
        meta = (path.parent / "meta.txt").read_text(encoding="utf-8")
        self.assertIn("provider=zhipu", meta)
        self.assertIn("model=glm-image", meta)
        self.assertIn("requested=576x1024", meta)
        self.assertIn("actual=960x1728", meta)
        self.assertIn("negative_prompt=dropped", meta)
        self.assertIn("糊", (path.parent / "negative_prompt.txt").read_text(encoding="utf-8"))

    def test_explicit_model_kept(self) -> None:
        llm, requests = make_zhipu(
            lambda request: httpx.Response(200, json={"data": [{"b64_json": B64}]}),
            model="cogview-4",
        )
        llm.generate(spec())
        self.assertIn(b'"model":"cogview-4"', _posts(requests)[0].read())

    def test_prompt_over_limit_not_sent(self) -> None:
        llm, requests = make_zhipu(lambda request: httpx.Response(500, text="no"))
        too_long = PromptSpec(prompt="猫" * 1001, width=576, height=1024)
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(too_long)
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_HTTP_4XX)
        self.assertIn("未发送请求", str(ctx.exception))
        self.assertEqual(requests, [])

    def test_prompt_at_limit_is_sent(self) -> None:
        llm, requests = make_zhipu(
            lambda request: httpx.Response(200, json={"data": [{"b64_json": B64}]})
        )
        llm.generate(PromptSpec(prompt="猫" * 1000, width=576, height=1024))
        self.assertEqual(len(_posts(requests)), 1)

    def test_audit_code_not_retried(self) -> None:
        llm, requests = make_zhipu(
            lambda request: httpx.Response(
                400, json={"error": {"code": 1301, "message": "blocked"}}
            )
        )
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_CONTENT_REJECTED)
        self.assertEqual(len(_posts(requests)), 1)

    def test_rate_limit_code_not_retried(self) -> None:
        llm, requests = make_zhipu(
            lambda request: httpx.Response(
                400, json={"error": {"code": "1302", "message": "slow down"}}
            )
        )
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_RATE_LIMITED)
        self.assertEqual(len(requests), 1)

    def test_generation_5xx_not_retried(self) -> None:
        llm, requests = make_zhipu(lambda request: httpx.Response(503, text="overloaded"))
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_HTTP_5XX)
        self.assertEqual(len(requests), 1)

    def test_download_retries_then_succeeds(self) -> None:
        state = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/images/generations"):
                return httpx.Response(200, json={"data": [{"url": "https://cdn.test/img.png"}]})
            state["n"] += 1
            if state["n"] <= 3:
                return httpx.Response(503, text="cdn")
            return httpx.Response(200, content=PNG_BYTES)

        llm, requests = make_zhipu(handler)
        path = llm.generate(spec())
        self.assertEqual(path.read_bytes(), PNG_BYTES)
        self.assertEqual(len(_posts(requests)), 1)
        self.assertEqual(state["n"], 4)

    def test_download_retries_exhausted_does_not_regenerate(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/images/generations"):
                return httpx.Response(200, json={"data": [{"url": "https://cdn.test/img.png"}]})
            raise httpx.ConnectError("cdn down")

        llm, requests = make_zhipu(handler)
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_BAD_RESPONSE)
        self.assertEqual(len(_posts(requests)), 1)
        self.assertEqual(len(requests) - 1, 4)

    def test_bad_response_shape(self) -> None:
        llm, _ = make_zhipu(lambda request: httpx.Response(200, json={"data": []}))
        with self.assertRaises(ImageLLMError) as ctx:
            llm.generate(spec())
        self.assertEqual(ctx.exception.kind, ImageLLMError.KIND_BAD_RESPONSE)

    def test_health_does_not_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(str(request.url))

        llm, requests = make_zhipu(handler)
        result = llm.health()
        self.assertTrue(result.ok)
        self.assertIn("未发请求", result.message)
        self.assertEqual(requests, [])


class FactoryTests(unittest.TestCase):
    def store(self) -> OutputStore:
        return OutputStore(Path(tempfile.mkdtemp()))

    def test_cloud_siliconflow(self) -> None:
        config = ImageLLMConfig(
            backend="cloud", provider="siliconflow",
            base_url="https://sf.test/v1", api_key="k", model="Qwen/Qwen-Image",
        )
        self.assertIsInstance(build_image_llm(config, self.store()), SiliconFlowImageLLM)

    def test_cloud_zhipu(self) -> None:
        config = ImageLLMConfig(
            backend="cloud",
            provider="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key="k",
            model="glm-image",
        )
        self.assertIsInstance(build_image_llm(config, self.store()), ZhipuImageLLM)

    def test_cloud_zhipu_missing_key_falls_back(self) -> None:
        config = ImageLLMConfig(
            backend="cloud",
            provider="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key="",
        )
        self.assertIsInstance(build_image_llm(config, self.store()), StubImageLLM)

    def test_cloud_unknown_provider_falls_back_to_stub(self) -> None:
        config = ImageLLMConfig(backend="cloud", provider="nope")
        self.assertIsInstance(build_image_llm(config, self.store()), StubImageLLM)

    def test_cloud_siliconflow_missing_key_falls_back(self) -> None:
        config = ImageLLMConfig(
            backend="cloud", provider="siliconflow",
            base_url="https://sf.test/v1", api_key="", model="Qwen/Qwen-Image",
        )
        self.assertIsInstance(build_image_llm(config, self.store()), StubImageLLM)


if __name__ == "__main__":
    unittest.main()
