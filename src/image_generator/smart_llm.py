from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from image_generator.config import SmartLLMConfig
from image_generator.intents import Intent, allowed_intents
from image_generator.session import PromptSpec, SessionState

DRAFT_SYSTEM = """你是文生图提示词编辑。根据用户的画面需求，写出给图像模型用的中文提示词。

要求：
- 只输出一个 JSON 对象，不要其它文字
- 字段：prompt, negative_prompt, width, height
- prompt：中文，写具体可见的主体、环境、构图、机位、光线、材质；少用空泛形容词
- negative_prompt：中文，只写需要避免的东西；没有则空字符串
- 用户没说尺寸时 width=576、height=1024
- 用户说了尺寸或比例时按用户的来；只说 9:16 则 576x1024，16:9 则 1024x576，1:1 则 1024x1024
- 不要把「用户说」「请生成」这类元话语写进 prompt
- 不要在 prompt 里写负向内容；负向只放 negative_prompt"""

PATCH_SYSTEM = """你是文生图提示词编辑。你要在上一版提示词上打补丁，而不是重写一张无关的新提示词。

要求：
- 只输出一个 JSON 对象，不要其它文字
- 字段：prompt, negative_prompt, width, height
- 只改「修改意见」点名的部分；未提到的主体、环境、构图、光线、服饰等全部保留
- 修改意见是编辑指令，不要把指令原文抄进 prompt（例如不要出现「请把光线改暗」这种句子）
- 若修改意见是增加需要避免的东西，写入 negative_prompt，并从 prompt 里去掉相反描述
- 用户没提尺寸则 width/height 与上一版完全相同
- 语言保持中文"""

CLASSIFY_SYSTEM = """你是生图助手的意图分类器。根据会话状态和用户一句话，判断意图。

只输出一个 JSON 对象，不要其它文字。字段：
- intent: 必须是本轮允许的意图之一，或 unknown
- instruction: 补丁或新需求里需要交给写提示词模块的自然语言；confirm_generate / cancel_generate 用空字符串
- confidence: high 或 low。不确定就 low
- reason: 短说明

规则：
- 用户明确要求先看提示词、先看 prompt、确认后再出图 → new_preview（仅当该意图被允许）
- 用户在描述一张新画面且未要求先看提示词 → new_generate
- 已有上一版提示词时，用户只说修改意见（更暗、换姿势、不要某某）→ 默认 patch_and_generate，除非他们要求先看提示词再用 patch_and_preview
- 已有图/提示词时，用户描述完全不同的新画面 → new_generate 或 new_preview
- 不要把意图写成画面提示词。instruction 只保留用户原意，不要扩写
- 无法可靠判断时 intent=unknown 且 confidence=low"""


class SmartLLMError(Exception):
    """User-visible failure talking to smart_llm. Never include API keys."""


# 任一对词同时在文本中出现即视为内容拒绝（顺序无关）。
# 正常画面提示词不含「生成/输出/照做」这类元话语，误报率趋近于零。
_REFUSAL_PAIRS = [
    ("不能", "生成"),
    ("无法", "生成"),
    ("无法", "帮助"),
    ("没法", "照做"),
    ("不能", "输出"),
    ("不能", "创建"),
    ("无法", "创建"),
    ("拒绝", "请求"),
    ("不符合", "规范"),
    ("违反", "政策"),
    ("cannot", "generate"),
    ("can't", "generate"),
    ("unable to", "generate"),
]


def _refusal_excerpt(text: str) -> str | None:
    """Return a short excerpt when text looks like a policy refusal, else None."""
    lowered = text.lower()
    for first, second in _REFUSAL_PAIRS:
        if first in lowered and second in lowered:
            return " ".join(text.split())[:100]
    return None


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        refusal = _refusal_excerpt(stripped)
        if refusal:
            raise SmartLLMError(f"smart_llm 拒绝了该请求: {refusal}")
        raise SmartLLMError("模型没有返回 JSON 对象")
    return stripped[start : end + 1]


def _as_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def spec_from_payload(payload: dict[str, Any], default_width: int = 576, default_height: int = 1024) -> PromptSpec:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise SmartLLMError("提示词为空")
    refusal = _refusal_excerpt(prompt)
    if refusal:
        raise SmartLLMError(f"smart_llm 拒绝了该请求: {refusal}")
    return PromptSpec(
        prompt=prompt,
        negative_prompt=str(payload.get("negative_prompt") or "").strip(),
        width=_as_int(payload.get("width"), default_width),
        height=_as_int(payload.get("height"), default_height),
    )


class SmartLLM:
    def __init__(self, config: SmartLLMConfig) -> None:
        self.config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )

    def chat_json(self, system: str, user: str, temperature: float) -> dict[str, Any]:
        try:
            response = self._client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
        except Exception as exc:
            raise SmartLLMError(f"smart_llm 请求失败: {exc}") from exc

        try:
            content = response.choices[0].message.content or ""
        except (IndexError, AttributeError) as exc:
            raise SmartLLMError("smart_llm 返回空内容") from exc
        try:
            return json.loads(_strip_json_fence(content))
        except json.JSONDecodeError as exc:
            raise SmartLLMError("smart_llm 返回的 JSON 无法解析") from exc

    def classify(
        self,
        text: str,
        state: SessionState,
        has_committed_spec: bool,
        current_prompt: str | None,
    ) -> tuple[Intent, str]:
        allowed = allowed_intents(state.value, has_committed_spec)
        allowed_names = [item.value for item in allowed]
        summary = (current_prompt or "").strip() or "（无）"
        if len(summary) > 300:
            summary = summary[:300] + "…"
        user = (
            f"当前状态：{state.value}\n"
            f"是否已有上一版提示词：{'是' if has_committed_spec else '否'}\n"
            f"本轮允许的意图：{', '.join(allowed_names)}\n"
            f"当前正向提示词摘要：\n{summary}\n\n"
            f"用户输入：\n{text}"
        )
        try:
            payload = self.chat_json(CLASSIFY_SYSTEM, user, temperature=0)
        except SmartLLMError as exc:
            if "JSON" in str(exc):
                return Intent.UNKNOWN, ""
            raise

        raw_intent = str(payload.get("intent") or "").strip()
        confidence = str(payload.get("confidence") or "").strip().lower()
        instruction = str(payload.get("instruction") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        try:
            intent = Intent(raw_intent)
        except ValueError:
            intent = Intent.UNKNOWN
        if intent not in allowed:
            intent = Intent.UNKNOWN
        if intent == Intent.UNKNOWN or confidence != "high":
            refusal = _refusal_excerpt(reason)
            if refusal:
                raise SmartLLMError(f"smart_llm 拒绝了该请求: {refusal}")
            return Intent.UNKNOWN, instruction
        return intent, instruction

    def draft(self, user_goal: str) -> PromptSpec:
        user = f"用户需求：\n{user_goal.strip()}"
        payload = self.chat_json(DRAFT_SYSTEM, user, temperature=0.4)
        return spec_from_payload(payload)

    def patch(self, old: PromptSpec, instruction: str) -> PromptSpec:
        user = (
            f"上一版正向提示词：\n{old.prompt}\n\n"
            f"上一版负向提示词：\n{old.negative_prompt}\n\n"
            f"上一版尺寸：\n{old.width}x{old.height}\n\n"
            f"修改意见：\n{instruction.strip()}"
        )
        payload = self.chat_json(PATCH_SYSTEM, user, temperature=0.4)
        return spec_from_payload(payload, default_width=old.width, default_height=old.height)
