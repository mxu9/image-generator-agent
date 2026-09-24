from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    NEW_GENERATE = "new_generate"
    NEW_PREVIEW = "new_preview"
    CONFIRM_GENERATE = "confirm_generate"
    PATCH_AND_GENERATE = "patch_and_generate"
    PATCH_AND_PREVIEW = "patch_and_preview"
    CANCEL_GENERATE = "cancel_generate"
    UNKNOWN = "unknown"


PATCH_INTENTS = {Intent.PATCH_AND_GENERATE, Intent.PATCH_AND_PREVIEW}

_NUMBER_RE = re.compile(
    r"^(?:选|第)?\s*(\d+)\s*(?:[.。、)）]?\s*)(.*)$",
)


def allowed_intents(state: str, has_committed_spec: bool) -> list[Intent]:
    if state == "preview":
        return [
            Intent.CONFIRM_GENERATE,
            Intent.PATCH_AND_GENERATE,
            Intent.PATCH_AND_PREVIEW,
            Intent.CANCEL_GENERATE,
        ]
    if has_committed_spec:
        return [
            Intent.NEW_GENERATE,
            Intent.NEW_PREVIEW,
            Intent.PATCH_AND_GENERATE,
            Intent.PATCH_AND_PREVIEW,
        ]
    return [Intent.NEW_GENERATE, Intent.NEW_PREVIEW]


def clarification_labels(intents: list[Intent]) -> list[str]:
    labels = {
        Intent.CONFIRM_GENERATE: "用当前提示词生图",
        Intent.PATCH_AND_GENERATE: "按你的修改意见改提示词后直接生图",
        Intent.PATCH_AND_PREVIEW: "改完提示词先给你看，确认后再生图",
        Intent.CANCEL_GENERATE: "取消这次生图",
        Intent.NEW_GENERATE: "按你刚才的描述直接生图",
        Intent.NEW_PREVIEW: "先给你看提示词，确认后再出图",
    }
    idle_with_spec = {
        Intent.NEW_GENERATE: "当作全新需求，直接生图",
        Intent.NEW_PREVIEW: "当作全新需求，先看提示词再出图",
        Intent.PATCH_AND_GENERATE: "按修改意见改当前提示词后直接生图",
        Intent.PATCH_AND_PREVIEW: "改完提示词先给你看，确认后再生图",
    }
    if Intent.NEW_GENERATE in intents and Intent.PATCH_AND_GENERATE in intents:
        labels = {**labels, **idle_with_spec}
    return [labels[item] for item in intents]


def build_clarification_question(intents: list[Intent]) -> str:
    lines = ["我没判断准你的意思。你是想：", ""]
    for index, label in enumerate(clarification_labels(intents), start=1):
        lines.append(f"{index}. {label}")
    lines.append("")
    lines.append("请直接说其中一种。")
    return "\n".join(lines)


def parse_numbered_choice(text: str, n_options: int) -> tuple[int, str] | None:
    stripped = text.strip()
    match = _NUMBER_RE.match(stripped)
    if not match:
        return None
    index = int(match.group(1))
    rest = match.group(2).strip()
    if index < 1 or index > n_options:
        return None
    return index, rest
