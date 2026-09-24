import unittest

from image_generator.commands import SlashCommandRegistry, build_registry, ExitRepl


class CommandTests(unittest.TestCase):
    def test_parse_exit(self) -> None:
        registry = build_registry()
        name, args = registry.parse(" /exit ")
        self.assertEqual(name, "exit")
        self.assertEqual(args, [])

    def test_parse_args(self) -> None:
        registry = SlashCommandRegistry()
        name, args = registry.parse("/foo bar baz")
        self.assertEqual(name, "foo")
        self.assertEqual(args, ["bar", "baz"])

    def test_exit_raises(self) -> None:
        registry = build_registry()
        with self.assertRaises(ExitRepl):
            registry.dispatch("/exit")

    def test_unknown(self) -> None:
        registry = build_registry()
        registry.dispatch("/foo")


if __name__ == "__main__":
    unittest.main()
