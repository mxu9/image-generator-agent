import io
import unittest
from contextlib import redirect_stdout

from image_generator.commands import (
    HELP_USAGE,
    ExitRepl,
    SlashCommandRegistry,
    build_registry,
)


def _registry() -> SlashCommandRegistry:
    return build_registry(
        lambda: None,
        lambda: None,
        lambda _args: None,
        lambda _args: None,
    )


def _capture(registry: SlashCommandRegistry, line: str) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        registry.dispatch(line)
    return buf.getvalue()


class CommandTests(unittest.TestCase):
    def test_parse_exit(self) -> None:
        registry = _registry()
        name, args = registry.parse(" /exit ")
        self.assertEqual(name, "exit")
        self.assertEqual(args, [])

    def test_parse_args(self) -> None:
        registry = SlashCommandRegistry()
        name, args = registry.parse("/foo bar baz")
        self.assertEqual(name, "foo")
        self.assertEqual(args, ["bar", "baz"])

    def test_exit_raises(self) -> None:
        registry = _registry()
        with self.assertRaises(ExitRepl):
            registry.dispatch("/exit")

    def test_unknown(self) -> None:
        registry = _registry()
        out = _capture(registry, "/foo")
        self.assertIn("未知命令: /foo", out)
        self.assertIn("/help", out)
        self.assertNotIn("可用命令:", out)

    def test_health_and_prompt_dispatch(self) -> None:
        calls: list[str] = []
        registry = build_registry(
            lambda: calls.append("health"),
            lambda: calls.append("prompt"),
            lambda _args: calls.append("sessions"),
            lambda _args: calls.append("load"),
        )
        registry.dispatch("/health")
        registry.dispatch("/prompt")
        self.assertEqual(calls, ["health", "prompt"])

    def test_sessions_and_load_dispatch(self) -> None:
        seen: list[tuple[str, list[str]]] = []
        registry = build_registry(
            lambda: None,
            lambda: None,
            lambda args: seen.append(("sessions", args)),
            lambda args: seen.append(("load", args)),
        )
        registry.dispatch("/sessions")
        registry.dispatch("/sessions 1")
        registry.dispatch("/load 20260925-143012")
        self.assertEqual(
            seen,
            [
                ("sessions", []),
                ("sessions", ["1"]),
                ("load", ["20260925-143012"]),
            ],
        )

    def test_registered_names(self) -> None:
        registry = _registry()
        self.assertEqual(
            sorted(registry.commands),
            ["exit", "health", "help", "load", "prompt", "sessions"],
        )

    def test_help_lists_all_commands(self) -> None:
        registry = _registry()
        out = _capture(registry, "/help")
        self.assertIn("斜杠命令：", out)
        for name in ("exit", "health", "help", "load", "prompt", "sessions"):
            self.assertIn(f"/{name}", out)
        self.assertIn(HELP_USAGE, out)

    def test_help_detail_and_leading_slash(self) -> None:
        registry = _registry()
        plain = _capture(registry, "/help sessions")
        slashed = _capture(registry, "/help /sessions")
        self.assertEqual(plain, slashed)
        self.assertIn("/sessions —", plain)
        self.assertIn("用法:", plain)
        self.assertIn("/sessions <编号或 id>", plain)
        self.assertIn("说明:", plain)
        self.assertIn("最新在前", plain)

    def test_help_unknown_command(self) -> None:
        registry = _registry()
        out = _capture(registry, "/help nope")
        self.assertIn("没有命令: /nope", out)
        self.assertIn(HELP_USAGE, out)

    def test_help_extra_args_use_first_only(self) -> None:
        registry = _registry()
        out = _capture(registry, "/help exit ignored")
        self.assertIn("/exit —", out)
        self.assertIn("Ctrl+C", out)


if __name__ == "__main__":
    unittest.main()
