from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from image_generator.output_store import MIN_PNG, OutputStore
from image_generator.session import PromptSpec


@dataclass
class HealthResult:
    ok: bool
    message: str = "ok"


class ImageLLM(Protocol):
    def generate(self, spec: PromptSpec) -> Path: ...
    def health(self) -> HealthResult: ...


class StubImageLLM:
    """v1 backend: write a tiny placeholder PNG. No HTTP."""

    def __init__(self, store: OutputStore) -> None:
        self.store = store

    def health(self) -> HealthResult:
        return HealthResult(ok=True, message="stub")

    def generate(self, spec: PromptSpec) -> Path:
        run_dir = self.store.make_run_dir()
        image_path = run_dir / "image.png"
        image_path.write_bytes(MIN_PNG)
        (run_dir / "prompt.txt").write_text(spec.prompt.rstrip() + "\n", encoding="utf-8")
        (run_dir / "negative_prompt.txt").write_text(
            spec.negative_prompt.rstrip() + "\n" if spec.negative_prompt.strip() else "",
            encoding="utf-8",
        )
        return image_path.resolve()
