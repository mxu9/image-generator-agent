# image_generator_agent

命令行 REPL：自然语言描述画面 → `smart_llm` 写中文提示词 → `image_llm` 出图 → 打印图片路径。

`smart_llm` 真打云端；`image_llm` 支持三种 backend：`sd`（本地，OpenAI 兼容
`/v1/images/generations`，含探活、错误分类与有限重试）、`cloud`（云端，当前
`IMAGE_LLM_PROVIDER=siliconflow` 真打 Qwen-Image、`zhipu` 真打 glm-image；按次计费，生图不自动重试）、
`stub`（占位 PNG，不发 HTTP）。

斜杠命令：`/exit` 退出、`/health` 检查生图服务、`/prompt` 显示当前提示词、`/sessions` 列出历史会话、`/load` 载入会话继续改图。

## 要求

- Python 3.11+
- uv
- 在 `.env` 填写 OpenAI 兼容的 `SMART_LLM_*`

## 启动

```powershell
cd <项目根目录>
copy .env.example .env
# 编辑 .env，填入 SMART_LLM_API_KEY / BASE_URL / MODEL
uv sync
uv run image-generator
```

输入画面描述即可。需要先看提示词时，在需求里明确说。退出输入 `/exit`。

## 配置

不要把 API key 或内网地址写进代码。只放 `.env`。
