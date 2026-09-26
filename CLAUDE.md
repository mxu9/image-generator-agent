# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

命令行 REPL agent：用户输入自然语言画面描述 → `smart_llm`（云端 OpenAI 兼容 Chat）分类意图并写/改中文提示词 → `image_llm` 出图（v2 真打 SD，可切 stub）→ 只打印图片绝对路径。设计文档在 `design/DESIGN.md`（含全部已锁定决策），实现计划在 `design/IMPLEMENTATION_PLAN.md`；**`design/` 目录被 .gitignore 忽略，不入仓库**（仅本地参考，改代码前先对照其合同）。

## 常用命令

```powershell
uv sync                    # 安装依赖（Python ≥3.11，用 uv 管理）
uv run image-generator     # 启动 REPL（入口：pyproject [project.scripts]）
uv run python -m image_generator   # 备用入口

# 测试（unittest，无 pytest）
uv run python -m unittest discover -s tests -v
uv run python -m unittest tests.test_intents                  # 单个文件
uv run python -m unittest tests.test_commands.ClassName.test_x  # 单个测试
```

运行前需 `copy .env.example .env` 并填 `SMART_LLM_*`（必填，缺了会警告但仍进 REPL）。测试不碰网络，不需要 .env。

## 架构

一次对话回合的管线（`cli.py` 的 `Agent.handle_line`）：

1. 行首 `/` → `commands.py` 斜杠注册表（`/exit` `/health` `/prompt`），**不送 LLM**
2. `smart_llm.classify()` 意图分类 → 返回 `{intent, instruction, confidence, reason}` JSON
3. 代码兜底（不信任模型自觉）：非 JSON / intent 不在枚举 / 不在当前 allowed 集合 / `confidence != high` → 一律视为 `unknown`
4. `unknown` → 追问编号菜单（`intents.build_clarification_question`），进入 `awaiting_clarification`；用户回答中的纯数字/`2 修改说明` 由 `parse_numbered_choice` 在代码里直接映射，不走分类器
5. 按 intent 分派：`draft`（新需求）或 `patch`（补丁）第二次调用 `smart_llm` 产出完整 PromptSpec → `image_llm.generate` 落盘

关键模块职责：

- `intents.py` — 意图枚举、各状态 `allowed_intents`、追问文案与编号解析。allowed 集合按会话状态变化：preview 下只允许 confirm/patch/cancel；idle 且已有 committed_spec 才允许 patch
- `session.py` — `SessionState` 状态机（IDLE / PREVIEW / AWAITING_CLARIFICATION）+ `PromptSpec`（prompt/negative_prompt/width/height，默认 576×1024）。`working_spec()` 决定补丁基于哪一版；`history_id` 指向当前历史会话
- `history.py` — 出图成功后写入 `sessions/<id>.json`（初稿+补丁链，图片只记相对路径）；`/sessions` 列表与详情、`/load` 载入最新版
- `smart_llm.py` — OpenAI 兼容客户端。三套独立 system prompt：`CLASSIFY_SYSTEM`（只分类，不写画面）、`DRAFT_SYSTEM`、`PATCH_SYSTEM`。所有调用要求只返回 JSON
- `image_llm.py` — `ImageLLM` Protocol + `SdImageLLM`（真打 OpenAI 兼容 `/v1/images/generations`：health 查 `/v1/models` 并校验 model 在列；generate 发 width/height + size，优先 `b64_json`、兜底 `url` 下载）+ `StubImageLLM`（占位 PNG）。`build_image_llm` 工厂按 `IMAGE_LLM_BACKEND` 分发：`sd` 且 base_url 非空→Sd、`cloud`→`cloud_llm`、否则 Stub。错误用 `ImageLLMError` 分类（connect/timeout/transport/4xx/5xx/OOM/限流/云端审核拒绝/坏响应）；SD 连接/超时/5xx 自动重试 2 次（间隔 2s），4xx/OOM/坏响应不重试
- `cloud_llm.py` — 云端生图。`ProviderProfile` 声明尺寸字段名/档位表/是否支持负向/响应键；任意宽高按对数比例距离映射到档位（平手取面积大）。`SiliconFlowImageLLM` 真打（`images[].url` 下载用**无鉴权**独立 client，URL 时效 1 小时须立即落盘）；**按次计费，任何错误都不自动重试**。`ZhipuImageLLM` 真打智谱 `glm-image`（`quality=hd`，无负向；下载失败最多重试 3 次，生图不重试；无免费探活）。负向提示词不支持的 provider 丢弃并在 `meta.txt` 标注（sidecar 保留原文）；每次出图写 `meta.txt`（provider/model/requested/actual 尺寸）
- `output_store.py` — 每次出图新建 `outputs/<YYYYMMDD-HHMMSS>/`（重名加 `-1`），同目录写 prompt.txt 调试文件

## 硬性设计约束（来自 design/DESIGN.md，改动需慎重）

- 补丁式改图：旧 PromptSpec 分字段 + 修改意见一起交给模型产出**完整新 PromptSpec**；禁止字符串拼接旧 prompt + 修改意见
- 分类与写提示词是**两次独立调用**，分类器不返回画面提示词，`instruction` 只保留用户原意
- 成功出图后**只打印绝对路径**，不打印 prompt（preview 状态打印提示词是唯一例外）
- `smart_llm` 失败不重试：打印错误、状态不变、留在 REPL；`image_llm` 仅对连接/超时/5xx 有限重试（2 次）
- 缺配置或 SD 探活失败不退出进程：启动警告后仍进 REPL
- 自然语言「退出」不当作退出指令；退出只认 `/exit`（Ctrl+C → exit 130）。斜杠命令现有 `/exit` `/health` `/prompt` `/sessions` `/load`
- 同义说法（「开始吧」「do it」= confirm）靠 `smart_llm` 判断，代码不维护关键词表
- API key / 内网地址只放 `.env`，不进代码和文档

## 代码风格

- 纯中文 UI 文案（终端输出、追问、错误信息）；文件 UTF-8，行长 ≤100 字符
- 已有代码用 dataclass + Protocol + 显式异常类型（`SmartLLMError` / `ExitRepl`），保持一致
- `cli.configure_stdio()` 处理 Windows 下 UTF-8 stdout，勿删
