from __future__ import annotations

import sys
from pathlib import Path

from image_generator.commands import ExitRepl, SlashCommandRegistry, build_registry
from image_generator.config import AppConfig, load_config, project_root
from image_generator.image_llm import ImageLLMError, ImageLLM, build_image_llm
from image_generator.intents import (
    Intent,
    PATCH_INTENTS,
    allowed_intents,
    build_clarification_question,
    parse_numbered_choice,
)
from image_generator.output_store import OutputStore
from image_generator.session import PromptSpec, Session, SessionState
from image_generator.smart_llm import SmartLLM, SmartLLMError


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def print_error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def print_preview(spec: PromptSpec) -> None:
    negative = spec.negative_prompt.strip() or "（无）"
    print("正向提示词：")
    print(spec.prompt.strip())
    print()
    print("负向提示词：")
    print(negative)
    print()
    print(f"尺寸：{spec.width}x{spec.height}")


class Agent:
    def __init__(
        self,
        config: AppConfig,
        session: Session,
        smart: SmartLLM | None,
        image_llm: ImageLLM,
        commands: SlashCommandRegistry,
    ) -> None:
        self.config = config
        self.session = session
        self.smart = smart
        self.image_llm = image_llm
        self.commands = commands

    def handle_line(self, line: str) -> bool:
        text = line.strip()
        if not text:
            return True
        if text.startswith("/"):
            self.commands.dispatch(text)
            return True
        if self.smart is None:
            print_error("SMART_LLM 未配置，无法处理自然语言。请在 .env 填写 SMART_LLM_* 后重试。")
            return True
        try:
            if self.session.awaiting_instruction_for is not None:
                self._run_intent(self.session.awaiting_instruction_for, text, text, from_number=True)
                return True
            if self.session.state == SessionState.AWAITING_CLARIFICATION:
                self._handle_clarification(text)
                return True
            self._handle_classified(text)
            return True
        except SmartLLMError as exc:
            print_error(str(exc))
            return True
        except ImageLLMError as exc:
            print_error(f"生图失败({exc.kind}): {exc}")
            return True
        except OSError as exc:
            print_error(f"写文件失败: {exc}")
            return True

    def cmd_health(self) -> None:
        result = self.image_llm.health()
        if result.ok:
            print(result.message)
        else:
            print(f"不可用: {result.message}")

    def cmd_prompt(self) -> None:
        spec = self.session.working_spec()
        if spec is None or not spec.is_valid():
            print("当前没有提示词。请先描述画面。")
            return
        print_preview(spec)

    def _classify_state(self) -> SessionState:
        if self.session.state == SessionState.AWAITING_CLARIFICATION:
            return self.session.pre_clarification_state
        return self.session.state

    def _allowed(self) -> list[Intent]:
        state = self._classify_state()
        return allowed_intents(state.value, self.session.has_committed_spec())

    def _handle_clarification(self, text: str) -> None:
        options = self.session.clarification_intents
        parsed = parse_numbered_choice(text, len(options)) if options else None
        if parsed is None:
            self._handle_classified(text)
            return
        index, rest = parsed
        intent = options[index - 1]
        self._run_intent(intent, rest, text, from_number=True)

    def _handle_classified(self, text: str) -> None:
        state = self._classify_state()
        current = None
        working = self.session.working_spec()
        if working is not None:
            current = working.prompt
        intent, instruction = self.smart.classify(
            text,
            state=state,
            has_committed_spec=self.session.has_committed_spec(),
            current_prompt=current,
        )
        if intent == Intent.UNKNOWN:
            self._ask_clarification(text)
            return
        source_for_goal = text
        self._run_intent(intent, instruction, source_for_goal, from_number=False)

    def _ask_clarification(self, text: str) -> None:
        if self.session.state != SessionState.AWAITING_CLARIFICATION:
            self.session.pre_clarification_state = self.session.state
            self.session.pending_user_text = text
        intents = self._allowed()
        question = build_clarification_question(intents)
        self.session.state = SessionState.AWAITING_CLARIFICATION
        self.session.clarification_intents = intents
        self.session.pending_question = question
        print(question)

    def _run_intent(self, intent: Intent, instruction: str, source_text: str, from_number: bool) -> None:
        if intent == Intent.UNKNOWN:
            self._ask_clarification(source_text)
            return

        patch_text = ""
        if intent in PATCH_INTENTS:
            patch_text = instruction.strip()
            if not patch_text and not from_number:
                patch_text = source_text.strip()
            if not patch_text:
                self.session.awaiting_instruction_for = intent
                print("请补充你的修改意见")
                return

        self.session.awaiting_instruction_for = None

        if intent == Intent.NEW_GENERATE:
            goal = self._goal_for_new(source_text, from_number)
            self._generate_new(goal, preview=False)
        elif intent == Intent.NEW_PREVIEW:
            goal = self._goal_for_new(source_text, from_number)
            self._generate_new(goal, preview=True)
        elif intent == Intent.CONFIRM_GENERATE:
            self._confirm_generate()
        elif intent == Intent.PATCH_AND_GENERATE:
            self._patch(patch_text, preview=False)
        elif intent == Intent.PATCH_AND_PREVIEW:
            self._patch(patch_text, preview=True)
        elif intent == Intent.CANCEL_GENERATE:
            self._cancel()
        else:
            self._ask_clarification(source_text)

    def _goal_for_new(self, source_text: str, from_number: bool) -> str:
        if from_number and self.session.pending_user_text:
            return self.session.pending_user_text
        return source_text

    def _finish_round_meta(self) -> None:
        self.session.clear_clarification()
        self.session.awaiting_instruction_for = None

    def _generate_new(self, goal: str, preview: bool) -> None:
        spec = self.smart.draft(goal)
        self.session.user_goal = goal
        self.session.active_spec = spec
        self._finish_round_meta()
        if preview:
            self.session.state = SessionState.PREVIEW
            print_preview(spec)
            return
        self._emit(spec)

    def _confirm_generate(self) -> None:
        spec = self.session.active_spec
        if spec is None or not spec.is_valid():
            print_error("当前没有可确认的提示词。请先描述画面。")
            return
        self._finish_round_meta()
        self._emit(spec)

    def _patch(self, instruction: str, preview: bool) -> None:
        base = self.session.working_spec()
        if base is None:
            print_error("还没有上一版提示词，无法按修改意见打补丁。请先描述画面。")
            return
        spec = self.smart.patch(base, instruction)
        self.session.active_spec = spec
        self._finish_round_meta()
        if preview:
            self.session.state = SessionState.PREVIEW
            print_preview(spec)
            return
        self._emit(spec)

    def _cancel(self) -> None:
        self.session.active_spec = self.session.committed_spec
        self._finish_round_meta()
        self.session.state = SessionState.IDLE
        print("已取消本次生图。")

    def _emit(self, spec: PromptSpec) -> None:
        path = self.image_llm.generate(spec)
        self.session.active_spec = spec
        self.session.committed_spec = spec
        self.session.last_image_path = path
        self.session.state = SessionState.IDLE
        print(str(path))


def main(argv: list[str] | None = None) -> int:
    del argv
    configure_stdio()
    root = project_root()
    config = load_config(root)
    if config.problems:
        print("WARNING: SMART_LLM 配置不完整，意图分类和写提示词会失败。", file=sys.stderr)
        for item in config.problems:
            print_error(item)

    store = OutputStore(root / "outputs")
    image_llm = build_image_llm(config.image, store)
    health = image_llm.health()
    if not health.ok:
        print(f"WARNING: image_llm 探活失败: {health.message}", file=sys.stderr)
    smart = SmartLLM(config.smart) if config.smart.ok else None
    agent = Agent(config, Session(), smart, image_llm, SlashCommandRegistry())
    agent.commands = build_registry(agent.cmd_health, agent.cmd_prompt)

    print("输入画面描述开始生图；需要先看提示词请在需求里说明。退出请输入 /exit。")
    try:
        while True:
            try:
                line = input("> ")
            except EOFError:
                print()
                return 0
            try:
                if not agent.handle_line(line):
                    return 0
            except ExitRepl:
                return 0
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
