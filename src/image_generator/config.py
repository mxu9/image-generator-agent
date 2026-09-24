from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
import os

PLACEHOLDER_VALUES = {"", "replace_me", "...", "your_key_here"}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _is_missing(value: str) -> bool:
    return value.strip() in PLACEHOLDER_VALUES


@dataclass
class SmartLLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0

    @property
    def problems(self) -> list[str]:
        issues: list[str] = []
        if _is_missing(self.base_url):
            issues.append("SMART_LLM_BASE_URL 未配置")
        if _is_missing(self.api_key):
            issues.append("SMART_LLM_API_KEY 未配置")
        if _is_missing(self.model):
            issues.append("SMART_LLM_MODEL 未配置")
        return issues

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class ImageLLMConfig:
    backend: str = "sd"
    base_url: str = ""
    api_key: str = ""
    model: str = "sd-cpp-local"
    timeout: float = 300.0
    connect_timeout: float = 15.0


@dataclass
class AppConfig:
    root: Path
    smart: SmartLLMConfig
    image: ImageLLMConfig
    problems: list[str] = field(default_factory=list)


def load_config(root: Path | None = None) -> AppConfig:
    root = root or project_root()
    load_dotenv(root / ".env")

    timeout_raw = _env("SMART_LLM_TIMEOUT", "60")
    try:
        smart_timeout = float(timeout_raw)
    except ValueError:
        smart_timeout = 60.0

    image_timeout_raw = _env("IMAGE_LLM_TIMEOUT", "300")
    image_connect_raw = _env("IMAGE_LLM_CONNECT_TIMEOUT", "15")
    try:
        image_timeout = float(image_timeout_raw)
    except ValueError:
        image_timeout = 300.0
    try:
        image_connect = float(image_connect_raw)
    except ValueError:
        image_connect = 15.0

    smart = SmartLLMConfig(
        base_url=_env("SMART_LLM_BASE_URL"),
        api_key=_env("SMART_LLM_API_KEY"),
        model=_env("SMART_LLM_MODEL"),
        timeout=smart_timeout,
    )
    image = ImageLLMConfig(
        backend=_env("IMAGE_LLM_BACKEND", "sd") or "sd",
        base_url=_env("IMAGE_LLM_BASE_URL"),
        api_key=_env("IMAGE_LLM_API_KEY"),
        model=_env("IMAGE_LLM_MODEL", "sd-cpp-local") or "sd-cpp-local",
        timeout=image_timeout,
        connect_timeout=image_connect,
    )
    return AppConfig(root=root, smart=smart, image=image, problems=list(smart.problems))
