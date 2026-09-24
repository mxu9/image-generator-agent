import unittest

from image_generator.config import SmartLLMConfig
from image_generator.intents import Intent
from image_generator.session import SessionState
from image_generator.smart_llm import (
    SmartLLM,
    SmartLLMError,
    _refusal_excerpt,
    _strip_json_fence,
    spec_from_payload,
)

# deepseek-v4-flash 实测拒绝输出（2026-09-24）
REAL_REFUSALS = [
    "我不能生成色情内容的提示词，这类图像不符合我的使用规范。",
    "抱歉，这个修改我没法照做——把人物改成全裸属于生成裸露内容，我不能输出这类提示词。",
    "我无法帮助生成或分类色情内容的出图请求。",
]

NORMAL_PROMPTS = [
    "一只橘猫趴在窗台上看雨，午后侧光",
    "赛博朋克街道夜景，霓虹倒影，眼神无法聚焦的仿生人",  # 「无法」单独出现，不成对
]


def make_smart() -> SmartLLM:
    return SmartLLM(SmartLLMConfig(base_url="http://localhost", api_key="k", model="m"))


class RefusalExcerptTests(unittest.TestCase):
    def test_real_refusal_texts_detected(self) -> None:
        for text in REAL_REFUSALS:
            self.assertIsNotNone(_refusal_excerpt(text), text)

    def test_normal_text_not_flagged(self) -> None:
        for text in NORMAL_PROMPTS:
            self.assertIsNone(_refusal_excerpt(text), text)


class StripFenceRefusalTests(unittest.TestCase):
    def test_refusal_text_raises_refusal_error(self) -> None:
        for text in REAL_REFUSALS:
            with self.assertRaises(SmartLLMError) as ctx:
                _strip_json_fence(text)
            self.assertIn("拒绝", str(ctx.exception))

    def test_plain_non_json_keeps_old_error(self) -> None:
        with self.assertRaises(SmartLLMError) as ctx:
            _strip_json_fence("今天天气不错")
        self.assertIn("JSON", str(ctx.exception))


class SpecRefusalTests(unittest.TestCase):
    def test_json_wrapped_refusal_rejected(self) -> None:
        with self.assertRaises(SmartLLMError) as ctx:
            spec_from_payload({"prompt": "无法生成该内容", "width": 576, "height": 1024})
        self.assertIn("拒绝", str(ctx.exception))

    def test_normal_prompt_passes(self) -> None:
        for prompt in NORMAL_PROMPTS:
            spec = spec_from_payload({"prompt": prompt})
            self.assertEqual(spec.prompt, prompt)


class ClassifyRefusalTests(unittest.TestCase):
    def test_plain_text_refusal_propagates(self) -> None:
        smart = make_smart()

        def fake(*_args: object, **_kwargs: object) -> dict:
            raise SmartLLMError("smart_llm 拒绝了该请求: 我不能生成色情内容")

        smart.chat_json = fake  # type: ignore[method-assign]
        with self.assertRaises(SmartLLMError) as ctx:
            smart.classify("画色情图", SessionState.IDLE, False, None)
        self.assertIn("拒绝", str(ctx.exception))

    def test_unknown_with_refusal_reason_raises(self) -> None:
        smart = make_smart()
        payload = {
            "intent": "unknown",
            "instruction": "",
            "confidence": "low",
            "reason": "用户想要生成成人色情图片，我无法生成，判断为unknown",
        }
        smart.chat_json = lambda *_a, **_k: payload  # type: ignore[method-assign]
        with self.assertRaises(SmartLLMError) as ctx:
            smart.classify("画色情图", SessionState.IDLE, False, None)
        self.assertIn("拒绝", str(ctx.exception))

    def test_unknown_with_normal_reason_still_unknown(self) -> None:
        smart = make_smart()
        payload = {"intent": "unknown", "instruction": "", "confidence": "low", "reason": "用户输入太短"}
        smart.chat_json = lambda *_a, **_k: payload  # type: ignore[method-assign]
        intent, instruction = smart.classify("嗯", SessionState.IDLE, False, None)
        self.assertEqual(intent, Intent.UNKNOWN)
        self.assertEqual(instruction, "")

    def test_confident_intent_unaffected(self) -> None:
        smart = make_smart()
        payload = {"intent": "new_generate", "instruction": "一只猫", "confidence": "high", "reason": ""}
        smart.chat_json = lambda *_a, **_k: payload  # type: ignore[method-assign]
        intent, _ = smart.classify("画一只猫", SessionState.IDLE, False, None)
        self.assertEqual(intent, Intent.NEW_GENERATE)


if __name__ == "__main__":
    unittest.main()
