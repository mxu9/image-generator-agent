# image_generator_agent 设计文档（第一版）

状态：待 review，未实现。  
范围：第一版主路径。第二版（真 SD、health、超时、重试）不在本文实现范围内，只在文末列出边界。

---

## 1. 目标

做一个命令行 REPL agent：用户用自然语言描述生图需求，agent 用云端 `smart_llm` 写/改中文提示词，再交给 `image_llm` 出图，并把图片路径告诉用户。用户可以按补丁方式改图，直到 `/exit`。

第一版要跑通的主路径：

```text
启动 REPL
  → 用户自然语言
  → smart_llm 意图分类（必须完整，含兜底追问）
  → smart_llm 写提示词或补丁
  → image_llm 出图（stub：写占位 PNG）
  → 只打印图片路径
  → 继续改图或 /exit
```

像 Codex / Claude Code 的终端插件：无 GUI，stdin/stdout 对话。

---

## 2. 非目标（第一版明确不做）

- 不实现本地 SD / 任何真生图 HTTP
- 不实现 health 探活、超时分类、自动重试、OOM 识别
- 不保留 xAI / Grok Imagine
- 不实现除 `/exit` 以外的斜杠命令（但框架要在）
- 不自动打开图片
- 不出图后默认打印提示词
- 不做 Web UI

---

## 3. 已锁定决策

| 项 | 决定 |
|---|---|
| 项目路径 | `C:\codex\image_generator_agent` |
| 包名 | `image_generator` |
| Python | ≥ 3.11，用 uv |
| 启动 | `uv run image-generator` |
| 写提示词 | `smart_llm`，云端 OpenAI 兼容 Chat Completions，真打 |
| 生图 | `image_llm` 统一接口；第一版只 stub |
| 旧仓库 | 不拷贝、不依赖 `grok-image-agent`，`image_llm` 重写 |
| 默认流程 | 写完提示词立刻出图 |
| 例外 | 用户明确要求先看提示词 → 进入预览 |
| 改图 | 补丁式：上一版 prompt + 修改说明 → `smart_llm` |
| 提示词语言 | 中文 |
| 默认尺寸 | 576×1024 |
| 成功输出 | 只打印图片路径 |
| 落盘 | `outputs\<YYYYMMDD-HHMMSS>\image.png`，不覆盖 |
| 出图 stub | 写入一张很小的占位 PNG |
| 失败 | 不重试；打印错误，留在 REPL |
| 退出 | `/exit` |
| 斜杠 | 框架先搭好，第一版只注册 `/exit` |
| 意图 | `smart_llm` 分类；无法识别则追问用户真实意图 |
| 预览阶段 | 确认生图 / 补丁后直接生图 / 补丁后再确认 / 取消生图；允许同义说法 |
| health / 超时 | stub，返回正常 |

---

## 4. 架构

```text
REPL (stdin/stdout)
  ├─ SlashCommandRegistry     # 框架；v1 仅 /exit
  ├─ Session                  # 状态、上一版 PromptSpec、最近图片路径
  ├─ IntentClassifier         # smart_llm；失败则追问
  ├─ PromptEngine             # smart_llm：draft / patch
  ├─ ImageLLM                 # 统一接口；v1 StubImageLLM
  └─ OutputStore              # outputs/<timestamp>/image.png
```

两套模型，配置分离、客户端分离：

- `smart_llm`：主力对话模型，负责意图分类、写提示词、补丁
- `image_llm`：生图模型接口，当前 stub，以后接本地 SD 或云端生图

不把 `smart_llm` 和 `image_llm` 的 key、url、timeout 混在一组变量里。

---

## 5. 配置

全部来自项目根目录 `.env`，不写进代码。

### 5.1 smart_llm（第一版必填，真用）

```text
SMART_LLM_BASE_URL=https://api.deepseek.com/v1
SMART_LLM_API_KEY=...
SMART_LLM_MODEL=deepseek-v4-flash
SMART_LLM_TIMEOUT=60
```

不设 `SMART_LLM_CONNECT_TIMEOUT`。按快响应处理，总超时 60s。

缺 `BASE_URL` / `API_KEY` / `MODEL`：启动时打印错误，**仍进入 REPL**（与探活失败策略一致）。随后第一次需要 `smart_llm` 的请求再失败并打印错误。

### 5.2 image_llm（第一版写入 `.env.example`，代码读得到，但不发 HTTP）

```text
IMAGE_LLM_BACKEND=sd
IMAGE_LLM_BASE_URL=http://127.0.0.1:9001
IMAGE_LLM_API_KEY=
IMAGE_LLM_MODEL=sd-cpp-local
IMAGE_LLM_TIMEOUT=300
IMAGE_LLM_CONNECT_TIMEOUT=15
```

第一版 `IMAGE_LLM_BACKEND` 只有文档意义；`StubImageLLM` 忽略这些值，仍写占位 PNG。第二版再启用。

### 5.3 启动时配置检查

- 检查 `SMART_LLM_*` 是否齐全（key 非空且不是占位符）
- 不齐全：警告 + 立刻打印错误原因 + 进入 REPL
- `GET /v1/models`：第一版 **health 为 stub，直接当作正常**
- 不因 health 失败而 `sys.exit`

---

## 6. 会话状态

同一 REPL 进程一份 `Session`：

| 字段 | 含义 |
|---|---|
| `state` | 见下表 |
| `user_goal` | 本轮最初的自然语言需求 |
| `prompt_spec` | 当前 `prompt` / `negative_prompt` / `width` / `height` |
| `last_image_path` | 最近一次成功写出的 PNG 路径 |
| `pending_question` | 追问用户意图时，问的那句话 |
| `allowed_intents` | 当前状态下允许的意图集合 |

### 6.1 状态机

```text
idle
  新需求直接出图 → generating → idle（成功后仍视为 idle，但已有 last_image_path / prompt_spec）
  新需求先看提示词 → preview

preview
  确认生图 → generating → idle
  补丁后直接生图 → generating → idle
  补丁后再确认 → preview（打印新提示词）
  取消生图 → idle（丢弃本轮未出图的 preview，保留更早的 last_image_path）

awaiting_clarification
  用户回答后重新分类
  仍无法识别 → 继续追问，不离开本状态
```

说明：出图成功后不单独做一个 `post_image` 状态。`idle` 且已有 `prompt_spec` 时，用户下一句可能是「改图」也可能是「全新需求」，由意图分类决定。

`generating` 对用户不可见，只是一次调用过程。第一版出图是瞬间 stub。

---

## 7. 意图分类（第一版必须完整）

### 7.1 意图枚举

| 意图 | 何时合法 | 含义 |
|---|---|---|
| `new_generate` | idle / 已有图的 idle | 新的生图需求，写提示词后直接出图 |
| `new_preview` | idle / 已有图的 idle | 新的生图需求，先打印提示词再等确认 |
| `confirm_generate` | preview | 用当前提示词出图 |
| `patch_and_generate` | preview，或 idle 且已有 prompt_spec | 按修改说明补丁，然后直接出图 |
| `patch_and_preview` | preview，或 idle 且已有 prompt_spec | 按修改说明补丁，打印提示词，等确认 |
| `cancel_generate` | preview | 取消本轮生图 |
| `unknown` | 任何状态 | 无法可靠判断 |

斜杠命令 **不走意图分类**。行首是 `/` 则进命令框架；未知命令报错，不把该行送给 `smart_llm`。

### 7.2 同义说法

不要求用户说固定原句。例如以下都应能进 `confirm_generate`：

- 确认生图
- 开始生图
- 开始吧
- do it
- ok，生成

`new_preview` 的触发是用户在需求里明确要先看提示词，例如「先给我提示词再出图」「先不要生图，让我看 prompt」。

`patch_*` 的修改说明（xxx）是给 `smart_llm` 的补丁意见，不是用户手写的完整 prompt。

### 7.3 分类合同

每次用户输入（非斜杠）调用 `smart_llm`，要求 **只返回 JSON**（不要散文）：

```json
{
  "intent": "patch_and_generate",
  "instruction": "光线再暗一点，不要正面光",
  "confidence": "high",
  "reason": "用户在已有图之后要求改光，并要直接出图"
}
```

- `intent`：必须是上一节枚举之一
- `instruction`：补丁/新需求里需要交给写提示词模块的自然语言；`confirm_generate` / `cancel_generate` 可为空字符串
- `confidence`：`high` | `low`
- `reason`：短说明，仅用于调试日志，不默认打给用户

额外兜底规则（代码，不依赖模型自觉）：

1. 返回非 JSON、缺字段、`intent` 不在枚举中 → 视为 `unknown`
2. `intent` 不在当前 `allowed_intents` 中 → 视为 `unknown`
3. `confidence == low` → 视为 `unknown`
4. `unknown` → **必须追问**，不得自行选一条路

### 7.4 当前状态允许的意图

| 状态 | 允许 |
|---|---|
| idle，尚无 prompt_spec | `new_generate`, `new_preview` |
| idle，已有 prompt_spec | `new_generate`, `new_preview`, `patch_and_generate`, `patch_and_preview` |
| preview | `confirm_generate`, `patch_and_generate`, `patch_and_preview`, `cancel_generate` |
| awaiting_clarification | 沿用进入追问前的那组 allowed_intents |

idle 且已有图时，用户说「重新做一张完全不同的……」应为 `new_generate`，不是 patch。

### 7.5 追问

当分类为 `unknown`：

1. `state = awaiting_clarification`
2. 按进入追问前的状态，列出**带编号**的可选意图（人话，不暴露枚举名）
3. 用户可以：
   - 只输入数字（`1` / `2` / `3`）
   - 输入数字 + 补充说明（`2 光线再暗一点`）
   - 不用数字，直接说具体意图（`取消这次`、`先让我看提示词`）
4. 仍 unknown → 再问，不退出 REPL，不出图

编号必须与 7.4 的允许意图**顺序一致**，方便用户用数字作答。

#### preview

```text
我没判断准你的意思。你是想：

1. 用当前提示词生图
2. 按你的修改意见改提示词后直接生图
3. 改完提示词先给你看，确认后再生图
4. 取消这次生图

请直接说其中一种。
```

对应：`1=confirm_generate`，`2=patch_and_generate`，`3=patch_and_preview`，`4=cancel_generate`。

#### idle，尚无 prompt_spec

```text
我没判断准你的意思。你是想：

1. 按你刚才的描述直接生图
2. 先给你看提示词，确认后再出图

请直接说其中一种。
```

对应：`1=new_generate`，`2=new_preview`。

#### idle，已有 prompt_spec

```text
我没判断准你的意思。你是想：

1. 当作全新需求，直接生图
2. 当作全新需求，先看提示词再出图
3. 按修改意见改当前提示词后直接生图
4. 改完提示词先给你看，确认后再生图

请直接说其中一种。
```

对应：`1=new_generate`，`2=new_preview`，`3=patch_and_generate`，`4=patch_and_preview`。

#### 数字怎么解析（代码兜底，不经过分类器）

进入追问后，若用户输入能解析成「选项编号」：

- 整行是 `1`、`2.`、`3、`、`选4` 这类 → 映射到上一次追问列表中的意图
- `2 光线再暗一点` → 意图取第 2 项，其余文本当作 `instruction`
- 编号越界 → 仍当 `unknown`，再问一遍

纯自然语言（没有可用编号）才再次交给 `smart_llm` 分类。

若选中的意图需要修改说明（`patch_and_generate` / `patch_and_preview`），但 `instruction` 为空：再问一句「请补充你的修改意见」，问到之前不出图、不改 prompt。

---

## 8. 提示词合同

### 8.1 输出结构

`PromptSpec`：

```text
prompt: str              # 中文正向提示词（给 image_llm 的画面描述）
negative_prompt: str     # 中文负向；没有则 ""
width: int               # 默认 576
height: int              # 默认 1024
```

`smart_llm` 写提示词 / 补丁时只返回 JSON：

```json
{
  "prompt": "……",
  "negative_prompt": "……",
  "width": 576,
  "height": 1024
}
```

规则：

- 用户没说尺寸 → `576×1024`
- 用户说了比例/尺寸 → 听用户的；无法解析则保持上一版（补丁）或默认（新稿）
- 解析失败（非 JSON、空 prompt）→ 打印错误，状态不变，不出图
- 写提示词与意图分类 **分开两次**调用

### 8.2 发给 smart_llm 的两类提示（分类 vs 写画面）

分类器只判断意图，不写画面提示词。写画面 / 补丁用下面两套。都要求：**只输出 JSON，不要 Markdown 围栏，不要解释。**

### 8.3 新稿（draft）

**system：**

```text
你是文生图提示词编辑。根据用户的画面需求，写出给图像模型用的中文提示词。

要求：
- 只输出一个 JSON 对象，不要其它文字
- 字段：prompt, negative_prompt, width, height
- prompt：中文，写具体可见的主体、环境、构图、机位、光线、材质；少用空泛形容词
- negative_prompt：中文，只写需要避免的东西；没有则空字符串
- 用户没说尺寸时 width=576、height=1024
- 用户说了尺寸或比例时按用户的来；只说 9:16 则 576x1024，16:9 则 1024x576，1:1 则 1024x1024
- 不要把「用户说」「请生成」这类元话语写进 prompt
- 不要在 prompt 里写负向内容；负向只放 negative_prompt
```

**user：**

```text
用户需求：
{user_goal}
```

`user_goal` 是用户最初那句画面描述（不要把 REPL 的追问菜单拼进去）。

### 8.4 补丁（patch）：修改意见怎么合并

合并发生在 **`smart_llm` 里**，不是把修改意见字符串拼到旧 prompt 末尾。

不要做：

```text
旧prompt + "。" + 修改意见     # 禁止
```

要做：把「上一版 PromptSpec」和「这一句修改意见」一起交给模型，让它产出**完整的新 PromptSpec**。已写对、用户没点名的部分原样保留；只改被点名的部分。

**system：**

```text
你是文生图提示词编辑。你要在上一版提示词上打补丁，而不是重写一张无关的新提示词。

要求：
- 只输出一个 JSON 对象，不要其它文字
- 字段：prompt, negative_prompt, width, height
- 只改「修改意见」点名的部分；未提到的主体、环境、构图、光线、服饰等全部保留
- 修改意见是编辑指令，不要把指令原文抄进 prompt（例如不要出现「请把光线改暗」这种句子）
- 若修改意见是增加需要避免的东西，写入 negative_prompt，并从 prompt 里去掉相反描述
- 用户没提尺寸则 width/height 与上一版完全相同
- 语言保持中文
```

**user：**

```text
上一版正向提示词：
{old_prompt}

上一版负向提示词：
{old_negative_prompt}

上一版尺寸：
{old_width}x{old_height}

修改意见：
{instruction}
```

`instruction` 来自意图分类（或用户在编号后附带的那句说明），例如「光线再暗一点，不要正面光」。

补丁后的 `prompt` 应是一份可直接拿去出图的完整中文提示词，而不是「在上一版基础上把光线调暗」这种操作说明。

### 8.5 调用次数

| 用户意图 | smart_llm 调用 |
|---|---|
| `new_generate` / `new_preview` | 分类（若未走数字兜底）+ draft |
| `patch_and_generate` / `patch_and_preview` | 分类（若未走数字兜底）+ patch |
| `confirm_generate` / `cancel_generate` | 至多一次分类；不调用 draft/patch |

---

## 9. 主流程

### 9.1 默认（直接出图）

```text
用户：描述画面
  → 分类 new_generate
  → draft PromptSpec
  → StubImageLLM.generate(spec)
  → 打印绝对路径
  → 记下 prompt_spec 与 last_image_path
```

### 9.2 先看提示词

```text
用户：描述画面，并要求先看提示词
  → 分类 new_preview
  → draft PromptSpec
  → 打印 prompt / negative_prompt / 尺寸（此时还不出图）
  → state = preview
```

预览后：

| 分类 | 行为 |
|---|---|
| `confirm_generate` | 用当前 spec 出图 |
| `patch_and_generate` | patch(spec, instruction) → 出图 |
| `patch_and_preview` | patch(spec, instruction) → 再打印提示词，留在 preview |
| `cancel_generate` | 不出图；state=idle；本轮 preview 的 spec 丢弃 |

预览阶段打印提示词是例外；出图成功后仍然只打印路径。

### 9.3 看图后改图（idle 且已有 spec）

```text
用户：改意见（可同义「直接出图」或「改完先让我看」）
  → patch_and_generate 或 patch_and_preview
  → 补丁后出图或再预览
```

若用户开始描述一张完全新的图 → `new_generate` / `new_preview`，替换 `user_goal`，重新 draft，不基于旧 prompt 补丁。

---

## 10. image_llm

统一接口（第一版就定下来，方便第二版换实现）：

```text
generate(spec: PromptSpec) -> Path
health() -> HealthResult   # v1 stub：ok
```

`StubImageLLM.generate`：

1. 建 `outputs/<YYYYMMDD-HHMMSS>/`（重名则加 `-1` 后缀）
2. 写入一张很小的合法 PNG，文件名为 `image.png`
3. 把 `prompt` / `negative_prompt` 写到同目录（供调试；**不打印到终端**，除非处于 preview）
4. 返回 `image.png` 的绝对路径

第一版不发 HTTP，不读 `IMAGE_LLM_BASE_URL` 做请求。

成功时终端仅：

```text
C:\codex\image_generator_agent\outputs\20260924-153000\image.png
```

路径用绝对路径，避免用户搞不清 cwd。

---

## 11. 斜杠命令框架

- 用户输入去掉行尾空白后，若以 `/` 开头 → 命令，不送分类器
- 解析：第一个 token 为命令名，其余为 args
- 注册表：`name -> handler`
- 未知命令：打印「未知命令: /foo」，提示 `/exit`
- 第一版只注册 `/exit`：干净退出进程（exit code 0）
- 以后 `/health`、`/prompt` 只往注册表加，不改 REPL 主循环

Ctrl+C：退出码 130，与常见 CLI 一致。

自然语言「退出」**不**当作退出；若分类器不能识别，走追问。退出只认 `/exit`。

---

## 12. 错误处理（第一版）

不自动重试。

| 情况 | 行为 |
|---|---|
| 缺 SMART_LLM 配置 | 启动警告并立刻打印错误；进 REPL |
| smart_llm 网络/HTTP/超时 | 打印错误；状态不变 |
| 意图 JSON 非法或 unknown | 追问真实意图 |
| 写提示词 JSON 非法或空 prompt | 打印错误；不出图 |
| 出图 stub 写文件失败 | 打印错误；不出「成功路径」 |
| 未知斜杠命令 | 提示未知命令 |
| KeyboardInterrupt | 退出 130 |

不把 API key 打到终端或日志。

---

## 13. 包结构（供 review）

```text
C:\codex\image_generator_agent\
  pyproject.toml
  .env.example
  .gitignore
  README.md
  docs\
    DESIGN.md
    IMPLEMENTATION_PLAN.md
  src\
    image_generator\
      __init__.py
      __main__.py          # python -m 备用；正式入口是 image-generator
      cli.py               # REPL
      config.py
      session.py
      commands.py          # 斜杠框架
      intents.py           # 枚举、allowed、追问文案
      smart_llm.py         # OpenAI 兼容客户端 + 分类 + draft/patch
      image_llm.py         # 协议 + StubImageLLM
      output_store.py
      placeholder.png 或代码内嵌最小 PNG 字节
  outputs\
    .gitkeep
```

CLI 入口：`pyproject.toml` 的 `[project.scripts] image-generator = "image_generator.cli:main"`。

---

## 14. smart_llm 调用约定

使用 OpenAI 兼容 Chat Completions：

- `base_url = SMART_LLM_BASE_URL`
- `api_key = SMART_LLM_API_KEY`
- `model = SMART_LLM_MODEL`
- `timeout = 60`
- `temperature` 分类用偏低（建议 0），写提示词可略高（建议 0.4）——实现时可写死，不进 `.env`

系统提示分两套：分类器一套、写提示词一套。不要把分类规则和摄影提示词写进同一个 system prompt。

---

## 15. 第二版边界（本文不实现）

- `image_llm` 真打本地 SD：`POST /v1/images/generations`
- 启动时 `GET /v1/models` 检查 model 是否存在
- 连接/读超时分类、HTTP 4xx/5xx 分类、CUDA OOM
- 有限自动重试
- `/health`、`/prompt`、`/retry`、`/open`
- 自然语言退出
- 云端生图 backend

---

## 16. 请 review 时重点看的点

1. 意图枚举是否够用、有没有要拆/要合并的
2. idle 已有图时，「新需求」vs「补丁」的划分是否符合你的用法
3. 分类失败追问的文案和循环是否可以
4. 预览才打印提示词、成功只打印路径，是否还要改
5. 包结构 / 两次 LLM 调用（先分类再写提示词）是否同意
