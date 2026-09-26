import unittest

from image_generator.commands import SlashCommandRegistry, build_registry, ExitRepl


def _registry() -> SlashCommandRegistry:
    return build_registry(
        lambda: None,
        lambda: None,
        lambda _args: None,
        lambda _args: None,
    )


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
        registry.dispatch("/foo")

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
            ["exit", "health", "load", "prompt", "sessions"],
        )


if __name__ == "__main__":
    unittest.main()