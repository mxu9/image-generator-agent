# image_generator_agent

命令行 REPL：自然语言描述画面 → `smart_llm` 写/改中文提示词 → `image_llm` 出图 → 打印图片绝对路径。

`smart_llm` 真打云端（OpenAI 兼容 Chat）。`image_llm` 三种 backend：

- `sd`：本地 OpenAI 兼容 `/v1/images/generations`（探活、错误分类；连接/超时/5xx 有限重试）
- `cloud`：云端按次计费，生图不自动重试
  - `IMAGE_LLM_PROVIDER=siliconflow` → Qwen-Image
  - `IMAGE_LLM_PROVIDER=zhipu` → glm-image（下载失败最多重试 3 次）
- `stub`：占位 PNG，不发 HTTP

出图成功会自动写入 `sessions/`（初稿 + 补丁链）。分类、写提示词、生图等待时 stderr 显示转圈和已等待秒数。

斜杠命令：`/exit` 退出、`/health` 检查生图服务、`/prompt` 显示当前提示词、`/sessions` 列出或查看历史会话、`/load` 载入会话最新版继续改图。

## 要求

- Python 3.11+
- uv
- 在 `.env` 填写 OpenAI 兼容的 `SMART_LLM_*`（必填；缺了会警告但仍进 REPL）

## 启动

```powershell
cd <项目根目录>
copy .env.example .env
# 编辑 .env，至少填 SMART_LLM_API_KEY / BASE_URL / MODEL
uv sync
uv run image-generator
```

输入画面描述即可。需要先看提示词时，在需求里明确说。退出输入 `/exit`。

## 配置

密钥和地址只放 `.env`，不要写进代码。示例见 `.env.example`。

生图 backend 由 `IMAGE_LLM_BACKEND` 决定：`sd` | `cloud` | `stub`。用智谱或 SiliconFlow 时必须设：

```text
IMAGE_LLM_BACKEND=cloud
IMAGE_LLM_PROVIDER=zhipu
IMAGE_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
IMAGE_LLM_API_KEY=
IMAGE_LLM_MODEL=glm-image
```

SiliconFlow 示例：

```text
IMAGE_LLM_BACKEND=cloud
IMAGE_LLM_PROVIDER=siliconflow
IMAGE_LLM_BASE_URL=https://api.siliconflow.cn/v1
IMAGE_LLM_API_KEY=
IMAGE_LLM_MODEL=Qwen/Qwen-Image
```

注意：只写 `IMAGE_LLM_PROVIDER`、不写 `IMAGE_LLM_BACKEND=cloud` 时，会按默认 `sd` 探活，容易误报「模型不在列表中」。

## 测试

```powershell
uv run python -m unittest discover -s tests -v
```

测试不碰网络，不需要 `.env`。
