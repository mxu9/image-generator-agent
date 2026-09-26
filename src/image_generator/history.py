from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from image_generator.session import PromptSpec

SCHEMA = 1
SUMMARY_MAX = 40

EMPTY_LIST_MESSAGE = "还没有保存过会话。出图成功后会自动保存。"
SESSIONS_USAGE = "用法: /sessions  或  /sessions <编号或 id>"
LOAD_USAGE = "用法: /load <编号或 id>"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def summarize_goal(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > SUMMARY_MAX:
        return collapsed[:SUMMARY_MAX] + "……"
    return collapsed


def _negative_display(text: str) -> str:
    stripped = text.strip()
    return stripped if stripped else "（无）"


@dataclass
class Revision:
    index: int
    kind: str
    instruction: str
    prompt: str
    negative_prompt: str
    width: int
    height: int
    image_path: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "instruction": self.instruction,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "image_path": self.image_path,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Revision:
        if not isinstance(data, dict):
            raise ValueError("revision is not an object")
        width = data.get("width")
        height = data.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise ValueError("revision size is invalid")
        prompt = str(data.get("prompt") or "")
        if not prompt.strip():
            raise ValueError("revision prompt is empty")
        return cls(
            index=int(data["index"]),
            kind=str(data.get("kind") or ""),
            instruction=str(data.get("instruction") or ""),
            prompt=prompt,
            negative_prompt=str(data.get("negative_prompt") or ""),
            width=width,
            height=height,
            image_path=str(data.get("image_path") or ""),
            created_at=str(data.get("created_at") or ""),
        )


@dataclass
class HistoryRecord:
    schema: int
    id: str
    created_at: str
    updated_at: str
    user_goal: str
    revisions: list[Revision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "user_goal": self.user_goal,
            "revisions": [item.to_dict() for item in self.revisions],
        }

    @classmethod
    def from_dict(cls, data: Any) -> HistoryRecord:
        if not isinstance(data, dict):
            raise ValueError("record is not an object")
        if data.get("schema") != SCHEMA:
            raise ValueError("unsupported schema")
        revisions_raw = data.get("revisions")
        if not isinstance(revisions_raw, list) or not revisions_raw:
            raise ValueError("revisions missing")
        revisions = [Revision.from_dict(item) for item in revisions_raw]
        history_id = str(data.get("id") or "")
        if not history_id:
            raise ValueError("id missing")
        return cls(
            schema=SCHEMA,
            id=history_id,
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            user_goal=str(data.get("user_goal") or ""),
            revisions=revisions,
        )


class HistoryStore:
    def __init__(self, root: Path, project_root: Path) -> None:
        self.root = root
        self.project_root = project_root
        self.root.mkdir(parents=True, exist_ok=True)

    def rel_image_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.project_root.resolve())
            return relative.as_posix()
        except ValueError:
            return resolved.as_posix()

    def absolute_image_path(self, stored: str) -> Path:
        path = Path(stored)
        if path.is_absolute():
            return path
        return (self.project_root / path).resolve()

    def allocate_id(self) -> str:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        candidate = stamp
        suffix = 1
        while (self.root / f"{candidate}.json").exists():
            candidate = f"{stamp}-{suffix}"
            suffix += 1
        return candidate

    def start(
        self,
        *,
        kind: str,
        instruction: str,
        spec: PromptSpec,
        image_path: Path,
        user_goal: str,
    ) -> HistoryRecord:
        created = now_iso()
        history_id = self.allocate_id()
        revision = Revision(
            index=1,
            kind=kind,
            instruction=instruction,
            prompt=spec.prompt,
            negative_prompt=spec.negative_prompt,
            width=spec.width,
            height=spec.height,
            image_path=self.rel_image_path(image_path),
            created_at=created,
        )
        record = HistoryRecord(
            schema=SCHEMA,
            id=history_id,
            created_at=created,
            updated_at=created,
            user_goal=user_goal,
            revisions=[revision],
        )
        self._write(record)
        return record

    def append(
        self,
        history_id: str,
        *,
        kind: str,
        instruction: str,
        spec: PromptSpec,
        image_path: Path,
    ) -> HistoryRecord:
        record = self.get_by_id(history_id)
        if record is None:
            raise FileNotFoundError(f"会话不存在: {history_id}")
        created = now_iso()
        revision = Revision(
            index=len(record.revisions) + 1,
            kind=kind,
            instruction=instruction,
            prompt=spec.prompt,
            negative_prompt=spec.negative_prompt,
            width=spec.width,
            height=spec.height,
            image_path=self.rel_image_path(image_path),
            created_at=created,
        )
        record.revisions.append(revision)
        record.updated_at = created
        self._write(record)
        return record

    def get_by_id(self, history_id: str) -> HistoryRecord | None:
        path = self.root / f"{history_id}.json"
        if not path.is_file():
            return None
        try:
            return self._load_path(path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return None

    def list_records(self) -> list[HistoryRecord]:
        items: list[HistoryRecord] = []
        for path in self.root.glob("*.json"):
            try:
                items.append(self._load_path(path))
            except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError):
                continue
        items.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return items

    def resolve(self, selector: str) -> HistoryRecord | None:
        token = selector.strip()
        if not token:
            return None
        records = self.list_records()
        if token.isdigit():
            index = int(token)
            if 1 <= index <= len(records):
                return records[index - 1]
            return None
        return self.get_by_id(token)

    def format_list(self, records: list[HistoryRecord] | None = None) -> str:
        items = self.list_records() if records is None else records
        if not items:
            return EMPTY_LIST_MESSAGE
        lines: list[str] = []
        for index, record in enumerate(items, start=1):
            n_rev = len(record.revisions)
            summary = summarize_goal(record.user_goal)
            lines.append(f"{index}. {record.id}  {n_rev}版  {summary}")
        return "\n".join(lines)

    def format_detail(self, record: HistoryRecord) -> str:
        lines = [
            f"会话 {record.id}",
            f"原始需求：{record.user_goal}",
            "",
        ]
        for rev in record.revisions:
            if rev.kind == "patch" and rev.instruction.strip():
                lines.append(f"#{rev.index} patch  {rev.instruction.strip()}")
            else:
                lines.append(f"#{rev.index} {rev.kind}")
            lines.append(f"尺寸：{rev.width}x{rev.height}")
            lines.append(f"正向：{rev.prompt}")
            lines.append(f"负向：{_negative_display(rev.negative_prompt)}")
            abs_path = self.absolute_image_path(rev.image_path)
            lines.append(f"图片：{abs_path}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def format_load(self, record: HistoryRecord) -> str:
        n_rev = len(record.revisions)
        latest = record.revisions[-1]
        abs_path = self.absolute_image_path(latest.image_path)
        return "\n".join(
            [
                f"已载入 {record.id}（{n_rev}版）",
                f"需求：{record.user_goal}",
                f"最近图片：{abs_path}",
            ]
        )

    def _load_path(self, path: Path) -> HistoryRecord:
        data = json.loads(path.read_text(encoding="utf-8"))
        return HistoryRecord.from_dict(data)

    def _write(self, record: HistoryRecord) -> None:
        path = self.root / f"{record.id}.json"
        tmp = self.root / f"{record.id}.json.tmp"
        payload = json.dumps(record.to_dict(), ensure_ascii=False, indent=2)
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, path)