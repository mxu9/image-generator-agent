import unittest

from image_generator.commands import SlashCommandRegistry, build_registry, ExitRepl


def _registry() -> SlashCommandRegistry:
    return build_registry(lambda: None, lambda: None)


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
        registry = build_registry(lambda: calls.append("health"), lambda: calls.append("prompt"))
        registry.dispatch("/health")
        registry.dispatch("/prompt")
        self.assertEqual(calls, ["health", "prompt"])

    def test_registered_names(self) -> None:
        registry = _registry()
        self.assertEqual(sorted(registry.commands), ["exit", "health", "prompt"])


if __name__ == "__main__":
    unittest.main()
