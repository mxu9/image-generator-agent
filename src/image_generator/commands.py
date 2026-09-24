from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


class ExitRepl(Exception):
    """Raised by /exit to leave the REPL cleanly."""


CommandHandler = Callable[[list[str]], None]


@dataclass
class SlashCommand:
    name: str
    handler: CommandHandler
    help: str = ""


@dataclass
class SlashCommandRegistry:
    commands: dict[str, SlashCommand] = field(default_factory=dict)

    def register(self, name: str, handler: CommandHandler, help: str = "") -> None:
        key = name.lower().lstrip("/")
        self.commands[key] = SlashCommand(name=key, handler=handler, help=help)

    def parse(self, line: str) -> tuple[str, list[str]]:
        parts = line.strip().split()
        if not parts:
            return "", []
        name = parts[0].lower().lstrip("/")
        return name, parts[1:]

    def dispatch(self, line: str) -> None:
        name, args = self.parse(line)
        command = self.commands.get(name)
        if command is None:
            known = ", ".join(f"/{item}" for item in sorted(self.commands))
            print(f"未知命令: /{name}")
            print(f"可用命令: {known}")
            return
        command.handler(args)


def handle_exit(_args: list[str]) -> None:
    raise ExitRepl


def build_registry(on_health: Callable[[], None], on_prompt: Callable[[], None]) -> SlashCommandRegistry:
    def handle_health(_args: list[str]) -> None:
        on_health()

    def handle_prompt(_args: list[str]) -> None:
        on_prompt()

    registry = SlashCommandRegistry()
    registry.register("exit", handle_exit, help="退出")
    registry.register("health", handle_health, help="检查生图服务状态")
    registry.register("prompt", handle_prompt, help="显示当前提示词")
    return registry
