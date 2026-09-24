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
            print(f"未知命令: /{name}")
            print("可用命令: /exit")
            return
        command.handler(args)


def handle_exit(_args: list[str]) -> None:
    raise ExitRepl


def build_registry() -> SlashCommandRegistry:
    registry = SlashCommandRegistry()
    registry.register("exit", handle_exit, help="退出")
    return registry
