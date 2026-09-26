import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

from image_generator.cli import Agent
from image_generator.commands import SlashCommandRegistry
from image_generator.config import AppConfig, ImageLLMConfig, SmartLLMConfig
from image_generator.history import (
    EMPTY_LIST_MESSAGE,
    HistoryStore,
    LOAD_USAGE,
    summarize_goal,
)
from image_generator.image_llm import StubImageLLM
from image_generator.output_store import OutputStore
from image_generator.session import PromptSpec, Session, SessionState


class FakeSmart:
    def draft(self, goal: str) -> PromptSpec:
        return PromptSpec(prompt=f"画面：{goal}", negative_prompt="糊")

    def patch(self, base: PromptSpec, instruction: str) -> PromptSpec:
        return PromptSpec(
            prompt=f"{base.prompt}；{instruction}",
            negative_prompt=base.negative_prompt,
            width=base.width,
            height=base.height,
        )


def _spec(prompt: str = "一只猫") -> PromptSpec:
    return PromptSpec(prompt=prompt, negative_prompt="糊", width=576, height=1024)


def _touch_image(root: Path, name: str = "image.png") -> Path:
    path = root / "outputs" / "run" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")
    return path


class HistoryStoreTests(unittest.TestCase):
    def test_roundtrip_relative_posix_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "sessions", project_root=root)
            image = _touch_image(root)
            record = store.start(
                kind="draft",
                instruction="",
                spec=_spec(),
                image_path=image,
                user_goal="画一只猫",
            )
            loaded = store.get_by_id(record.id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.revisions[0].image_path, "outputs/run/image.png")
            self.assertFalse(list(store.root.glob("*.tmp")))
            raw = json.loads((store.root / f"{record.id}.json").read_text(
                encoding="utf-8"
            ))
            self.assertEqual(raw["schema"], 1)
            self.assertEqual(raw["revisions"][0]["kind"], "draft")

    def test_append_grows_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "sessions", project_root=root)
            image1 = _touch_image(root)
            record = store.start(
                kind="draft",
                instruction="",
                spec=_spec(),
                image_path=image1,
                user_goal="画一只猫",
            )
            image2 = _touch_image(root, "image2.png")
            updated = store.append(
                record.id,
                kind="patch",
                instruction="光线再暗一点",
                spec=_spec("一只暗光猫"),
                image_path=image2,
            )
            self.assertEqual(len(updated.revisions), 2)
            self.assertEqual(updated.revisions[1].kind, "patch")
            self.assertEqual(updated.revisions[1].instruction, "光线再暗一点")
            self.assertGreaterEqual(updated.updated_at, record.updated_at)

    def test_bad_json_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "sessions", project_root=root)
            (store.root / "bad.json").write_text("{", encoding="utf-8")
            (store.root / "also.json").write_text(
                json.dumps({"schema": 1, "id": "x"}),
                encoding="utf-8",
            )
            self.assertEqual(store.list_records(), [])
            self.assertEqual(store.format_list(), EMPTY_LIST_MESSAGE)

    def test_id_collision_adds_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "sessions", project_root=root)
            stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
            (store.root / f"{stamp}.json").write_text("{}", encoding="utf-8")
            record = store.start(
                kind="draft",
                instruction="",
                spec=_spec(),
                image_path=_touch_image(root),
                user_goal="猫",
            )
            self.assertTrue(record.id.startswith(stamp))
            self.assertNotEqual(record.id, stamp)

    def test_resolve_number_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "sessions", project_root=root)
            first = store.start(
                kind="draft",
                instruction="",
                spec=_spec("旧"),
                image_path=_touch_image(root),
                user_goal="旧需求",
            )
            second = store.start(
                kind="draft",
                instruction="",
                spec=_spec("新"),
                image_path=_touch_image(root, "b.png"),
                user_goal="新需求",
            )
            listed = store.list_records()
            self.assertEqual(listed[0].id, second.id)
            self.assertEqual(store.resolve("1").id, second.id)
            self.assertEqual(store.resolve("2").id, first.id)
            self.assertEqual(store.resolve(second.id).id, second.id)
            self.assertIsNone(store.resolve("9"))
            self.assertIsNone(store.resolve("missing"))

    def test_summarize_goal(self) -> None:
        self.assertEqual(summarize_goal("  画一只   猫  "), "画一只 猫")
        long = "字" * 41
        self.assertEqual(summarize_goal(long), ("字" * 40) + "……")

    def test_format_detail_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "sessions", project_root=root)
            image = _touch_image(root)
            record = store.start(
                kind="draft",
                instruction="",
                spec=_spec(),
                image_path=image,
                user_goal="画一只猫",
            )
            store.append(
                record.id,
                kind="patch",
                instruction="加帽子",
                spec=_spec("戴帽猫"),
                image_path=_touch_image(root, "hat.png"),
            )
            record = store.get_by_id(record.id)
            assert record is not None
            detail = store.format_detail(record)
            self.assertIn("会话 " + record.id, detail)
            self.assertIn("原始需求：画一只猫", detail)
            self.assertIn("#1 draft", detail)
            self.assertIn("#2 patch  加帽子", detail)
            self.assertIn("正向：戴帽猫", detail)
            self.assertIn("负向：糊", detail)
            loaded = store.format_load(record)
            self.assertIn(f"已载入 {record.id}（2版）", loaded)
            self.assertIn("需求：画一只猫", loaded)
            self.assertIn("最近图片：", loaded)


class AgentHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        store = OutputStore(self.root / "outputs")
        self.history = HistoryStore(self.root / "sessions", project_root=self.root)
        config = AppConfig(
            root=self.root,
            smart=SmartLLMConfig(base_url="x", api_key="x", model="x"),
            image=ImageLLMConfig(backend="stub"),
        )
        self.agent = Agent(
            config,
            Session(),
            FakeSmart(),  # type: ignore[arg-type]
            StubImageLLM(store),
            SlashCommandRegistry(),
            self.history,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_preview_does_not_save(self) -> None:
        self.agent._generate_new("一只猫", preview=True)
        self.assertEqual(self.history.list_records(), [])
        self.assertEqual(self.agent.session.state, SessionState.PREVIEW)

    def test_generate_saves_draft(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.agent._generate_new("一只猫", preview=False)
        records = self.history.list_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].revisions[0].kind, "draft")
        self.assertEqual(records[0].user_goal, "一只猫")
        self.assertIn(str(self.agent.session.last_image_path), buf.getvalue())
        self.assertNotIn("已保存", buf.getvalue())

    def test_preview_then_patch_first_kind_is_patch(self) -> None:
        self.agent._generate_new("一只猫", preview=True)
        self.agent._patch("加一顶帽子", preview=False)
        record = self.history.list_records()[0]
        self.assertEqual(record.revisions[0].kind, "patch")
        self.assertEqual(record.revisions[0].instruction, "加一顶帽子")
        self.assertEqual(record.user_goal, "一只猫")

    def test_preview_patch_then_confirm_keeps_instruction(self) -> None:
        self.agent._generate_new("一只猫", preview=True)
        self.agent._patch("加帽子", preview=True)
        self.agent._confirm_generate()
        record = self.history.list_records()[0]
        self.assertEqual(len(record.revisions), 1)
        self.assertEqual(record.revisions[0].kind, "patch")
        self.assertEqual(record.revisions[0].instruction, "加帽子")

    def test_patch_appends_same_file(self) -> None:
        self.agent._generate_new("一只猫", preview=False)
        history_id = self.agent.session.history_id
        self.agent._patch("加帽子", preview=False)
        records = self.history.list_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, history_id)
        self.assertEqual(len(records[0].revisions), 2)
        self.assertEqual(records[0].revisions[1].kind, "patch")

    def test_new_after_existing_creates_second_file(self) -> None:
        self.agent._generate_new("猫", preview=False)
        self.agent._generate_new("狗", preview=False)
        records = self.history.list_records()
        self.assertEqual(len(records), 2)
        goals = {item.user_goal for item in records}
        self.assertEqual(goals, {"猫", "狗"})

    def test_load_then_patch_appends(self) -> None:
        self.agent._generate_new("猫", preview=False)
        history_id = self.agent.session.history_id
        assert history_id is not None
        self.agent.session = Session()
        self.agent.session.state = SessionState.PREVIEW
        self.agent.session.active_spec = PromptSpec(prompt="未提交")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.agent.cmd_load([history_id])
        self.assertEqual(self.agent.session.state, SessionState.IDLE)
        self.assertEqual(self.agent.session.history_id, history_id)
        self.assertNotEqual(self.agent.session.active_spec.prompt, "未提交")
        self.agent._patch("暗一点", preview=False)
        records = self.history.list_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0].revisions), 2)
        self.assertIn("已载入", buf.getvalue())

    def test_load_then_new_creates_file(self) -> None:
        self.agent._generate_new("猫", preview=False)
        history_id = self.agent.session.history_id
        self.agent.cmd_load([history_id])
        self.agent._generate_new("狗", preview=False)
        records = self.history.list_records()
        self.assertEqual(len(records), 2)

    def test_load_without_args_prints_usage(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.agent.cmd_load([])
        self.assertEqual(buf.getvalue().strip(), LOAD_USAGE)

    def test_cancel_does_not_save(self) -> None:
        self.agent._generate_new("一只猫", preview=True)
        self.agent._cancel()
        self.assertEqual(self.history.list_records(), [])

    def test_save_failure_still_prints_path(self) -> None:
        def boom(**_kwargs):
            raise OSError("disk full")

        self.agent.history.start = boom  # type: ignore[method-assign]
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.agent._generate_new("一只猫", preview=False)
        self.assertTrue(self.agent.session.last_image_path)
        self.assertIn(str(self.agent.session.last_image_path), out.getvalue())
        self.assertIn("会话保存失败", err.getvalue())
        self.assertIn("disk full", err.getvalue())


if __name__ == "__main__":
    unittest.main()