from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from image_generator.config import ImageLLMConfig
from image_generator.output_store import MIN_PNG, OutputStore
from image_generator.session import PromptSpec


@dataclass
class HealthResult:
    ok: bool
    message: str = "ok"


class ImageLLM(Protocol):
    def generate(self, spec: PromptSpec) -> Path: ...
    def health(self) -> HealthResult: ...


class ImageLLMError(Exception):
    """User-visible image backend failure, classified by kind. Never includes API keys."""

    KIND_CONNECT = "connect_error"
    KIND_TIMEOUT = "timeout"
    KIND_TRANSPORT = "transport_error"
    KIND_HTTP_4XX = "http_4xx"
    KIND_HTTP_5XX = "http_5xx"
    KIND_OOM = "cuda_oom"
    KIND_RATE_LIMITED = "rate_limited"
    KIND_CONTENT_REJECTED = "content_rejected"
    KIND_BAD_RESPONSE = "bad_response"

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind in {self.KIND_CONNECT, self.KIND_TIMEOUT, self.KIND_TRANSPORT, self.KIND_HTTP_5XX}


_OOM_MARKERS = ("out of memory", "cuda error", "cuda", "显存")


def classify_transport_error(exc: httpx.HTTPError) -> ImageLLMError:
    """Map an httpx transport exception to a user-visible ImageLLMError."""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return ImageLLMError(ImageLLMError.KIND_CONNECT, f"无法连接生图服务: {exc}")
    if isinstance(exc, httpx.TimeoutException):
        return ImageLLMError(ImageLLMError.KIND_TIMEOUT, f"生图服务请求超时: {exc}")
    return ImageLLMError(ImageLLMError.KIND_TRANSPORT, f"网络传输错误: {exc.__class__.__name__}: {exc}")


def _write_prompt_files(run_dir: Path, spec: PromptSpec) -> None:
    (run_dir / "prompt.txt").write_text(spec.prompt.rstrip() + "\n", encoding="utf-8")
    (run_dir / "negative_prompt.txt").write_text(
        spec.negative_prompt.rstrip() + "\n" if spec.negative_prompt.strip() else "",
        encoding="utf-8",
    )


class StubImageLLM:
    """Offline backend: write a tiny placeholder PNG. No HTTP."""

    def __init__(self, store: OutputStore) -> None:
        self.store = store

    def health(self) -> HealthResult:
        return HealthResult(ok=True, message="stub")

    def generate(self, spec: PromptSpec) -> Path:
        run_dir = self.store.make_run_dir()
        image_path = run_dir / "image.png"
        image_path.write_bytes(MIN_PNG)
        _write_prompt_files(run_dir, spec)
        return image_path.resolve()


class SdImageLLM:
    """Real backend: OpenAI-compatible /v1/images/generations (sd-cpp-server)."""

    RETRY_LIMIT = 2
    RETRY_DELAY = 2.0

    def __init__(
        self,
        config: ImageLLMConfig,
        store: OutputStore,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.store = store
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(config.timeout, connect=config.connect_timeout),
            transport=transport,
        )

    def health(self) -> HealthResult:
        try:
            response = self._client.get("/v1/models", timeout=10.0)
        except httpx.HTTPError as exc:
            return HealthResult(ok=False, message=f"无法连接 SD 服务: {exc.__class__.__name__}: {exc}")
        if response.status_code != 200:
            return HealthResult(ok=False, message=f"/v1/models HTTP {response.status_code}")
        try:
            payload = response.json()
            ids = [str(item.get("id") or "") for item in payload.get("data", [])]
        except (ValueError, AttributeError) as exc:
            return HealthResult(ok=False, message=f"/v1/models 返回的 JSON 无法解析: {exc}")
        if self.config.model not in ids:
            listed = ", ".join(item for item in ids if item) or "（空）"
            return HealthResult(
                ok=False,
                message=f"模型 {self.config.model} 不在服务列表中: {listed}",
            )
        return HealthResult(ok=True, message=f"ok, model={self.config.model}")

    def generate(self, spec: PromptSpec) -> Path:
        body = {
            "model": self.config.model,
            "prompt": spec.prompt,
            "negative_prompt": spec.negative_prompt,
            "width": spec.width,
            "height": spec.height,
            "size": f"{spec.width}x{spec.height}",
            "n": 1,
            "response_format": "b64_json",
        }
        last_error: ImageLLMError | None = None
        for attempt in range(1 + self.RETRY_LIMIT):
            if attempt:
                time.sleep(self.RETRY_DELAY)
            try:
                response = self._client.post("/v1/images/generations", json=body)
            except httpx.HTTPError as exc:
                error = classify_transport_error(exc)
            else:
                error = self._response_error(response)
                if error is None:
                    return self._save(spec, response)
            assert last_error is not None or error is not None
            last_error = error
            if not error.retryable:
                raise error
        raise last_error

    def _response_error(self, response: httpx.Response) -> ImageLLMError | None:
        if response.status_code < 400:
            return None
        text = response.text.strip()[:500] or "(空响应体)"
        lowered = text.lower()
        if any(marker in lowered for marker in _OOM_MARKERS):
            return ImageLLMError(ImageLLMError.KIND_OOM, f"SD 服务显存不足(OOM): {text}")
        if response.status_code < 500:
            return ImageLLMError(ImageLLMError.KIND_HTTP_4XX, f"HTTP {response.status_code}: {text}")
        return ImageLLMError(ImageLLMError.KIND_HTTP_5XX, f"HTTP {response.status_code}: {text}")

    def _save(self, spec: PromptSpec, response: httpx.Response) -> Path:
        try:
            payload = response.json()
            first = payload["data"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ImageLLMError(ImageLLMError.KIND_BAD_RESPONSE, f"生图响应缺少 data[0]: {exc}") from exc

        run_dir = self.store.make_run_dir()
        image_path = run_dir / "image.png"
        b64 = first.get("b64_json")
        if b64:
            try:
                image_path.write_bytes(base64.b64decode(b64))
            except (ValueError, TypeError) as exc:
                raise ImageLLMError(ImageLLMError.KIND_BAD_RESPONSE, f"b64_json 解码失败: {exc}") from exc
        elif first.get("url"):
            self._download(first["url"], image_path)
        else:
            raise ImageLLMError(ImageLLMError.KIND_BAD_RESPONSE, "生图响应既没有 b64_json 也没有 url")
        _write_prompt_files(run_dir, spec)
        return image_path.resolve()

    def _download(self, url: str, image_path: Path) -> None:
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise ImageLLMError(ImageLLMError.KIND_BAD_RESPONSE, f"下载图片失败: {exc}") from exc
        if response.status_code != 200:
            raise ImageLLMError(
                self.KIND_BAD_RESPONSE,
                f"下载图片失败: HTTP {response.status_code}",
            )
        image_path.write_bytes(response.content)


def build_image_llm(config: ImageLLMConfig, store: OutputStore) -> ImageLLM:
    backend = config.backend.strip().lower()
    if backend == "cloud":
        # 延迟 import：cloud_llm 依赖本模块的 ImageLLMError / StubImageLLM
        from image_generator.cloud_llm import build_cloud_image_llm

        return build_cloud_image_llm(config, store)
    if backend == "sd" and config.base_url.strip():
        return SdImageLLM(config, store)
    return StubImageLLM(store)
