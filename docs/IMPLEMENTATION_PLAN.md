# image_generator_agent 第一版实现计划

状态：待 review，未实现。  
依据：`docs/DESIGN.md` 已锁定决策。  
本计划只覆盖第一版；真 SD、health、超时、重试不在此列。

---

## 0. 验收标准（第一版做完长什么样）

在 `C:\codex\image_generator_agent` 下：

```powershell
cd C:\codex\image_generator_agent
copy .env.example .env
# 填入 SMART_LLM_* 后：
uv sync
uv run image-generator
```

然后人工走通：

1. 输入自然语言需求（不要求先看提示词）→ 终端只出现一张占位 PNG 的**绝对路径**
2. 再说修改意见 → 再出一张新路径（新时间戳目录，不覆盖）
3. 新开一句「先看提示词再出图」的需求 → 打印中文 prompt / negative / 尺寸，**此时无新路径**
4. 预览下说同义「开始吧」→ 出图并打印路径
5. 预览下说修改说明并要求确认后再出图 → 再打印提示词，仍不出图；再说确认才出图
6. 预览下说取消 → 不出图，可继续输入
7. 故意说含糊的话 → agent 追问真实意图，不擅自出图
8. `/exit` 退出；`/foo` 提示未知命令
9. 不填 API key 启动 → 警告并立刻报错，但仍能进 REPL；`/exit` 仍可用

不验收：真 SD 图、health 探活、超时重试。

---

## 1. 实现顺序

严格按依赖往下做。每一步结束时项目应可运行到该步为止（哪怕后面是 NotImplemented 的清晰错误）。

### Step 1 — uv 骨架

- 建项目：Python `>=3.11`
- `src/image_generator` 包
- `pyproject.toml`：依赖 `openai`、`python-dotenv`；脚本 `image-generator = "image_generator.cli:main"`
- `.gitignore`：`.env`、`.venv`、`__pycache__`、`outputs/*`（保留 `outputs/.gitkeep`）
- `.env.example`：写入 DESIGN 第 5 节的两组变量
- `README.md`：如何 `uv sync`、填 `.env`、`uv run image-generator`（不写 LAN IP、不写 key）

不做业务。

### Step 2 — 配置加载

- `config.py`：读 `.env`
- `SmartLLMConfig` / `ImageLLMConfig` 数据类
- 缺 SMART_LLM 必填项：结构化错误（给 CLI 打印），**不抛到让进程直接死掉**（启动策略是进 REPL）
- 不在此时打 `/v1/models`（health stub）

### Step 3 — REPL + 斜杠框架 + `/exit`

- `cli.py`：循环读一行
- UTF-8 输出（Windows 下避免中文乱码）
- `commands.py`：注册表、解析 `/cmd args`
- 注册 `/exit`
- 未知 `/xxx` 报错
- 空行忽略
- Ctrl+C → exit 130
- 此时尚无 LLM：非斜杠输入可先回复「尚未接入」——下一步立刻接上，review 通过后实现时不要把这句占位留在最终第一版

### Step 4 — Session 与 OutputStore

- `session.py`：状态枚举、PromptSpec、Session
- `output_store.py`：按 `YYYYMMDD-HHMMSS` 建目录，冲突加 `-1`
- 内嵌最小合法 PNG 字节，写出 `image.png`
- 同目录写 `prompt.txt` / `negative_prompt.txt`（调试用，CLI 默认不打印）

### Step 5 — smart_llm 客户端

- `smart_llm.py`：OpenAI SDK，`base_url` / `api_key` / `model` / `timeout=60`
- 方法：
  - `chat_json(system, user) -> dict`
  - 非 JSON、HTTP 错误、超时 → 抛/返回可打印的错误类型，由 CLI 打印后继续循环
- 不打日志里的 key

### Step 6 — 意图分类（必须完整）

- `intents.py`：枚举、各状态 `allowed_intents`、**带编号的追问文案**（与 DESIGN 7.5 一致）
- 分类 system prompt：只做分类，输出 DESIGN 第 7.3 节 JSON
- 代码兜底：解析失败 / 非法 intent / 不在 allowed / `confidence=low` → `unknown`
- `unknown` → `awaiting_clarification` + 编号菜单
- 追问状态下：纯数字 / `2 修改说明` **代码直接映射**，不走分类器；需要 instruction 但为空则再问修改意见
- 无编号的自然语言才再次交给 `smart_llm` 分类；仍 unknown 继续问

本步可先用固定假会话打分类（不真正出图），但最终第一版要接到主循环。

### Step 7 — draft / patch 提示词

- 使用 DESIGN 8.3 / 8.4 的 system + user 模板（原文进代码，不要临场发挥）
- 补丁是把旧 PromptSpec 与修改意见分字段交给模型，禁止字符串拼接旧 prompt
- `draft(user_goal) -> PromptSpec`
- `patch(old_spec, instruction) -> PromptSpec`
- JSON 非法或 `prompt` 为空 → 错误，不出图，状态不变

### Step 8 — StubImageLLM 接到主循环

按 DESIGN 第 9 节把状态机接满：

- `new_generate` → draft → stub generate → 打印绝对路径
- `new_preview` → draft → 打印提示词 → preview
- `confirm_generate` → generate
- `patch_and_generate` → patch → generate
- `patch_and_preview` → patch → 打印提示词 → preview
- `cancel_generate` → idle，丢弃本轮 preview spec
- 成功路径：**只打印绝对路径**，不打印 prompt

### Step 9 — 手动验收

按第 0 节清单过一遍。缺的补上。不写自动化测试也可以；若加测试，只加不碰网络的：命令解析、状态 allowed_intents、JSON 兜底、OutputStore 目录命名。

---

## 2. 文件与职责

| 文件 | 职责 |
|---|---|
| `src/image_generator/cli.py` | REPL 主循环、打印、把一行分给命令或意图 |
| `src/image_generator/commands.py` | 斜杠注册与 `/exit` |
| `src/image_generator/config.py` | `.env` |
| `src/image_generator/session.py` | 状态与 PromptSpec |
| `src/image_generator/intents.py` | 意图枚举、允许集合、追问 |
| `src/image_generator/smart_llm.py` | 云端客户端、分类、draft、patch |
| `src/image_generator/image_llm.py` | 协议 + Stub |
| `src/image_generator/output_store.py` | 目录与占位 PNG |
| `docs/DESIGN.md` | 设计（已写） |
| `docs/IMPLEMENTATION_PLAN.md` | 本计划 |

不把 LAN IP 或 key 写进任何提交文件。

---

## 3. 实现时的约束

- 第一版 **两次** `smart_llm` 调用：先分类，再 draft/patch。确认生图 / 取消 不第二次调用写提示词
- 不以「看起来像确认」的本地关键词表替代分类器；同义说法靠 `smart_llm`。代码只做 JSON/枚举/allowed/low-confidence 兜底
- 分类器不得在 JSON 里直接返回改写后的完整 prompt（那是 draft/patch 的事）。`instruction` 只承载用户原意
- stub 出图也要新建时间戳目录，方便以后对照补丁
- 不在第一版实现 `GET /v1/models`、不实现 SD POST
- 不拷贝 `grok-image-agent` 的 `generate.py`

---

## 4. 建议的依赖

```text
openai>=1.40.0
python-dotenv>=1.0.1
```

不引入 Web 框架、不引入 requests（第一版 chat 走 openai SDK 即可）。

---

## 5. 不在本计划内的后续（第二版草稿，勿做）

1. `SdImageLLM`：health `GET /` + `GET /v1/models`，generate POST
2. 启动探活失败：警告 + 立刻打印 + 仍进 REPL（行为已设计，第一版用 stub 代替真探活）
3. 超时/连接错误分类与有限重试
4. `/health` `/prompt`

---

## 6. Review 通过后的落地顺序

1. 按 Step 1 起 uv 项目（不再改 DESIGN 除非你在 review 里改）
2. Step 2–8 按序实现
3. 按第 0 节和你一起过手动清单

在你对本文 + `DESIGN.md` 点头之前，不写业务代码、不起 uv 项目。
