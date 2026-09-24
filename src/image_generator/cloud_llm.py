from __future__ import annotations

import base64
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from image_generator.config import ImageLLMConfig
from image_generator.image_llm import (
    HealthResult,
    ImageLLM,
    ImageLLMError,
    StubImageLLM,
    _write_prompt_files,
    classify_transport_error,
)
from image_generator.output_store import OutputStore
from image_generator.session import PromptSpec

# 云端按次计费：生图请求不自动重试（连接错误也不重试），失败直接报错由用户重发。
# 与 SdImageLLM（免费本地服务，重试 2 次）策略不同。

# 审核判定关键词：4xx 响应体命中即视为内容拒绝
_AUDIT_MARKERS = ("敏感", "审核", "violation", "policy", "content filter", "20012")


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    size_field: str  # 请求体里尺寸字段名："image_size" | "size"
    sizes: tuple[tuple[int, int], ...]  # 服务端允许的档位
    supports_negative: bool
    response_key: str  # 响应里图片数组的键："images" | "data"


SILICONFLOW = ProviderProfile(
    name="siliconflow",
    size_field="image_size",
    sizes=(
        (1328, 1328),
        (1664, 928),
        (928, 1664),
        (1472, 1140),
        (1140, 1472),
        (1584, 1056),
        (1056, 1584),
    ),
    supports_negative=True,  # Qwen/Qwen-Image 支持 negative_prompt（官方 API 文档）
    response_key="images",
)


def map_size(profile: ProviderProfile, width: int, height: int) -> tuple[int, int]:
    """把任意宽高映射到档位：宽高比（对数距离）最接近者优先，平手取面积大者。"""
    target = math.log(width / height)
    return min(
        profile.sizes,
        key=lambda size: (abs(math.log(size[0] / size[1]) - target), -(size[0] * size[1])),
    )


def _write_meta(
    run_dir: Path,
    provider: str,
    model: str,
    requested: tuple[int, int],
    actual: tuple[int, int],
    negative: str,
) -> None:
    lines = [
        f"provider={provider}",
        f"model={model}",
        f"requested={requested[0]}x{requested[1]}",
        f"actual={actual[0]}x{actual[1]}",
        f"negative_prompt={negative}",
    ]
    (run_dir / "meta.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


class SiliconFlowImageLLM:
    """Cloud backend: SiliconFlow POST /v1/images/generations. 不自动重试。"""

    def __init__(
        self,
        config: ImageLLMConfig,
        store: OutputStore,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.profile = SILICONFLOW
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.api_key}"} if config.api_key else {},
            timeout=httpx.Timeout(config.timeout, connect=config.connect_timeout),
            transport=transport,
        )
        # 下载用独立 client：不带 Authorization（URL 通常是第三方 CDN，时效仅 1 小时，须立即落盘）
        self._download_client = httpx.Client(timeout=60.0, transport=transport)

    def health(self) -> HealthResult:
        try:
            response = self._client.get("/models", timeout=10.0)
        except httpx.HTTPError as exc:
            return HealthResult(ok=False, message=f"无法连接 {self.profile.name}: {exc}")
        if response.status_code != 200:
            return HealthResult(ok=False, message=f"/models HTTP {response.status_code}")
        try:
            payload = response.json()
            ids = [str(item.get("id") or "") for item in payload.get("data", [])]
        except (ValueError, AttributeError):
            return HealthResult(ok=False, message="/models 返回的 JSON 无法解析")
        if self.config.model not in ids:
            return HealthResult(
                ok=False,
                message=f"模型 {self.config.model} 不在服务列表中（共 {len(ids)} 个模型）",
            )
        return HealthResult(ok=True, message=f"ok, model={self.config.model}")

    def generate(self, spec: PromptSpec) -> Path:
        actual = map_size(self.profile, spec.width, spec.height)
        body: dict[str, object] = {
            "model": self.config.model,
            "prompt": spec.prompt,
            self.profile.size_field: f"{actual[0]}x{actual[1]}",
            "batch_size": 1,
        }
        negative_state = "none"
        if spec.negative_prompt.strip():
            if self.profile.supports_negative:
                body["negative_prompt"] = spec.negative_prompt
                negative_state = "sent"
            else:
                negative_state = "dropped"  # 云端不支持负向：丢弃，sidecar 保留原文，meta 标注

        try:
            response = self._client.post("/images/generations", json=body)
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc
        error = self._response_error(response)
        if error is not None:
            raise error
        return self._save(spec, response, actual, negative_state)

    def _response_error(self, response: httpx.Response) -> ImageLLMError | None:
        if response.status_code < 400:
            return None
        text = response.text.strip()[:500] or "(空响应体)"
        if response.status_code == 429:
            return ImageLLMError(ImageLLMError.KIND_RATE_LIMITED, f"云端限流(429): {text}")
        lowered = text.lower()
        if response.status_code < 500 and any(marker in lowered or marker in text for marker in _AUDIT_MARKERS):
            return ImageLLMError(ImageLLMError.KIND_CONTENT_REJECTED, f"云端生图拒绝: {text}")
        if response.status_code < 500:
            return ImageLLMError(ImageLLMError.KIND_HTTP_4XX, f"HTTP {response.status_code}: {text}")
        return ImageLLMError(ImageLLMError.KIND_HTTP_5XX, f"HTTP {response.status_code}: {text}")

    def _save(
        self,
        spec: PromptSpec,
        response: httpx.Response,
        actual: tuple[int, int],
        negative_state: str,
    ) -> Path:
        try:
            payload = response.json()
            items = payload.get(self.profile.response_key) or payload.get("data") or payload.get("images")
            first = items[0]  # type: ignore[index]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ImageLLMError(
                ImageLLMError.KIND_BAD_RESPONSE, f"生图响应缺少图片条目: {exc}"
            ) from exc

        run_dir = self.store.make_run_dir()
        image_path = run_dir / "image.png"
        b64 = first.get("b64_json")
        url = first.get("url")
        if b64:
            try:
                image_path.write_bytes(base64.b64decode(b64))
            except (ValueError, TypeError) as exc:
                raise ImageLLMError(ImageLLMError.KIND_BAD_RESPONSE, f"b64_json 解码失败: {exc}") from exc
        elif url:
            self._download(str(url), image_path)
        else:
            raise ImageLLMError(ImageLLMError.KIND_BAD_RESPONSE, "生图响应既没有 b64_json 也没有 url")
        _write_prompt_files(run_dir, spec)
        _write_meta(run_dir, self.profile.name, self.config.model, (spec.width, spec.height), actual, negative_state)
        return image_path.resolve()

    def _download(self, url: str, image_path: Path) -> None:
        try:
            response = self._download_client.get(url)
        except httpx.HTTPError as exc:
            raise ImageLLMError(ImageLLMError.KIND_BAD_RESPONSE, f"下载图片失败: {exc}") from exc
        if response.status_code != 200:
            raise ImageLLMError(ImageLLMError.KIND_BAD_RESPONSE, f"下载图片失败: HTTP {response.status_code}")
        image_path.write_bytes(response.content)


class ZhipuStubImageLLM(StubImageLLM):
    """zhipu provider 占位：接口齐全但不发请求，等接入真实现（智谱无 negative_prompt）。"""

    def health(self) -> HealthResult:
        return HealthResult(ok=True, message="zhipu stub（未接真实现）")

    def generate(self, spec: PromptSpec) -> Path:
        path = super().generate(spec)
        negative = "dropped" if spec.negative_prompt.strip() else "none"
        _write_meta(path.parent, "zhipu", "(stub)", (spec.width, spec.height), (spec.width, spec.height), negative)
        return path


def build_cloud_image_llm(config: ImageLLMConfig, store: OutputStore) -> ImageLLM:
    provider = (config.provider or "").strip().lower()
    if provider == "siliconflow":
        if not config.base_url.strip() or not config.api_key.strip():
            print(
                "WARNING: backend=cloud 且 provider=siliconflow 需要 IMAGE_LLM_BASE_URL 与 IMAGE_LLM_API_KEY，已降级为 stub。",
                file=sys.stderr,
            )
            return StubImageLLM(store)
        return SiliconFlowImageLLM(config, store)
    if provider == "zhipu":
        return ZhipuStubImageLLM(store)
    if provider:
        print(
            f"WARNING: 未知 IMAGE_LLM_PROVIDER={provider}（可选 zhipu/siliconflow），已降级为 stub。",
            file=sys.stderr,
        )
    else:
        print(
            "WARNING: backend=cloud 需要 IMAGE_LLM_PROVIDER（zhipu/siliconflow），已降级为 stub。",
            file=sys.stderr,
        )
    return StubImageLLM(store)
