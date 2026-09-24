# image_generator_agent

命令行 REPL：自然语言描述画面 → `smart_llm` 写中文提示词 → `image_llm` 出图 → 打印图片路径。

第一版：`smart_llm` 真打云端；出图是占位 PNG（不调用 SD）。

## 要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 在 `.env` 填写 OpenAI 兼容的 `SMART_LLM_*`

## 启动

```powershell
cd C:\codex\image_generator_agent
copy .env.example .env
# 编辑 .env，填入 SMART_LLM_API_KEY / BASE_URL / MODEL
uv sync
uv run image-generator
```

输入画面描述即可。需要先看提示词时，在需求里明确说。退出输入 `/exit`。

## 配置

不要把 API key 或内网地址写进代码。只放 `.env`。
