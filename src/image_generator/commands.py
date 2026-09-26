from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


class ExitRepl(Exception):
    """Raised by /exit to leave the REPL cleanly."""


CommandHandler = Callable[[list[str]], None]

HELP_USAGE = "用法: /help  或  /help <命令>"


@dataclass
class SlashCommand:
    name: str
    handler: CommandHandler
    help: str = ""
    usage: str = ""
    detail: str = ""


@dataclass
class SlashCommandRegistry:
    commands: dict[str, SlashCommand] = field(default_factory=dict)

    def register(
        self,
        name: str,
        handler: CommandHandler,
        help: str = "",
        usage: str = "",
        detail: str = "",
    ) -> None:
        key = name.lower().lstrip("/")
        self.commands[key] = SlashCommand(
            name=key,
            handler=handler,
            help=help,
            usage=usage,
            detail=detail,
        )

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
            print("输入 /help 查看可用命令。")
            return
        command.handler(args)

    def format_help_list(self) -> str:
        lines = ["斜杠命令："]
        names = sorted(self.commands)
        width = max((len(name) for name in names), default=0)
        for name in names:
            cmd = self.commands[name]
            pad = " " * (width - len(name))
            summary = cmd.help or "（无简介）"
            lines.append(f"  /{name}{pad}  {summary}")
        lines.append("")
        lines.append(HELP_USAGE)
        return "\n".join(lines)

    def format_help_detail(self, selector: str) -> str:
        key = selector.strip().lower().lstrip("/")
        if not key:
            return self.format_help_list()
        cmd = self.commands.get(key)
        if cmd is None:
            return f"没有命令: /{key}\n{HELP_USAGE}"
        title = f"/{cmd.name} — {cmd.help}" if cmd.help else f"/{cmd.name}"
        lines = [title]
        if cmd.usage.strip():
            lines.append("")
            lines.append("用法:")
            for item in cmd.usage.strip().splitlines():
                lines.append(f"  {item.strip()}")
        if cmd.detail.strip():
            lines.append("")
            lines.append("说明:")
            for item in cmd.detail.strip().splitlines():
                text = item.strip()
                if text:
                    lines.append(f"  {text}")
        return "\n".join(lines)


def handle_exit(_args: list[str]) -> None:
    raise ExitRepl


def build_registry(
    on_health: Callable[[], None],
    on_prompt: Callable[[], None],
    on_sessions: Callable[[list[str]], None],
    on_load: Callable[[list[str]], None],
) -> SlashCommandRegistry:
    def handle_health(_args: list[str]) -> None:
        on_health()

    def handle_prompt(_args: list[str]) -> None:
        on_prompt()

    def handle_sessions(args: list[str]) -> None:
        on_sessions(args)

    def handle_load(args: list[str]) -> None:
        on_load(args)

    registry = SlashCommandRegistry()

    def handle_help(args: list[str]) -> None:
        if not args:
            print(registry.format_help_list())
            return
        print(registry.format_help_detail(args[0]))

    registry.register(
        "exit",
        handle_exit,
        help="退出 REPL",
        usage="/exit",
        detail=(
            "自然语言「退出」不会退出。\n"
            "Ctrl+C 退出码 130。"
        ),
    )
    registry.register(
        "health",
        handle_health,
        help="检查生图服务状态",
        usage="/health",
        detail=(
            "调用当前 image_llm 的探活结果。\n"
            "智谱无免费探活时，可能只提示未发请求。"
        ),
    )
    registry.register(
        "help",
        handle_help,
        help="显示斜杠命令帮助",
        usage="/help\n/help <命令>",
        detail=(
            "无参数时列出全部命令。\n"
            "带参数时显示该命令的用法和说明。"
        ),
    )
    registry.register(
        "load",
        handle_load,
        help="载入历史会话最新一版",
        usage="/load <编号或 id>",
        detail=(
            "编号按 /sessions 当前列表（最新在前）。\n"
            "载入后可继续补丁改图；全新需求会新建会话。\n"
            "只载入最新一版，不支持载入中间某一版。"
        ),
    )
    registry.register(
        "prompt",
        handle_prompt,
        help="显示当前提示词",
        usage="/prompt",
        detail=(
            "打印当前工作中的正向/负向提示词与尺寸。\n"
            "没有提示词时会提示先描述画面。"
        ),
    )
    registry.register(
        "sessions",
        handle_sessions,
        help="列出或查看历史会话",
        usage="/sessions\n/sessions <编号或 id>",
        detail=(
            "编号按当前列表（最新在前）。纯数字当编号，其它当 id。\n"
            "只查看，不改变当前 REPL 状态。\n"
            "出图成功后才会自动保存会话。"
        ),
    )
    return registry
