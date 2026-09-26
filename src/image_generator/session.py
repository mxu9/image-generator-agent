from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from image_generator.intents import Intent


class SessionState(str, Enum):
    IDLE = "idle"
    PREVIEW = "preview"
    AWAITING_CLARIFICATION = "awaiting_clarification"


@dataclass
class PromptSpec:
    prompt: str
    negative_prompt: str = ""
    width: int = 576
    height: int = 1024

    def is_valid(self) -> bool:
        return bool(self.prompt.strip())


@dataclass
class Session:
    state: SessionState = SessionState.IDLE
    user_goal: str | None = None
    committed_spec: PromptSpec | None = None
    active_spec: PromptSpec | None = None
    last_image_path: Path | None = None
    pending_question: str | None = None
    pending_user_text: str | None = None
    clarification_intents: list[Intent] = field(default_factory=list)
    pre_clarification_state: SessionState = SessionState.IDLE
    awaiting_instruction_for: Intent | None = None
    history_id: str | None = None
    pending_kind: str | None = None
    pending_instruction: str = ""

    def has_committed_spec(self) -> bool:
        return self.committed_spec is not None

    def working_spec(self) -> PromptSpec | None:
        if self.state == SessionState.PREVIEW and self.active_spec is not None:
            return self.active_spec
        if self.pre_clarification_state == SessionState.PREVIEW and self.active_spec is not None:
            return self.active_spec
        return self.active_spec or self.committed_spec

    def clear_clarification(self) -> None:
        self.pending_question = None
        self.pending_user_text = None
        self.clarification_intents = []
        if self.state == SessionState.AWAITING_CLARIFICATION:
            self.state = self.pre_clarification_state