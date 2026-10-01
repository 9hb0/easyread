# 精读论文文档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每篇论文增加一个手动触发、最多 5000 字、可查看和下载的精读 Markdown 文档，并在设置中配置精读模型和提示词。

**Architecture:** 新增 `easyread/deepread.py` 负责论文内容整理、提示词拼接、结果截断和文件读写；复用现有 `chat_models.engine_cfg`、`engines.run` 和小任务队列。前端新增精读设置页和阅读页侧栏，文献库菜单只负责触发或打开对应论文。

**Tech Stack:** Python 标准库、现有 EasyRead JSON 文件存储、现有后台线程队列、原生 HTML/CSS/JavaScript、现有 Markdown/KaTeX 渲染。

**Spec:** `docs/superpowers/specs/2026-10-01-deepread-design.md`

## Global Constraints

- 只允许用户主动触发精读，不能挂到导入、翻译完成或打开论文流程。
- 后端强制保证最终文档字符数不超过 5000。
- 精读模型只保存模型 ID，复用问 AI 已保存的 BASE_URL、Key 和推理强度。
- Key 不得出现在任何前端响应、论文状态文件或精读 Markdown 中。
- 生成失败不能覆盖已有的 `deepread.md`。
- 保留当前工作区已有改动，不做无关回滚或重构。

## Review Focus

- 手动触发和重复点击：同一篇论文已有 queued/running 任务时不能重复排队。
- 长模型输出：模型返回超过 5000 字时，落盘结果必须仍然不超过 5000 字。
- 旧结果保护：重新生成失败时，旧文档和旧成功状态内容仍可查看。
- 未翻译论文：中文字段为空时，精读输入必须回退到英文原文，而不是生成空文档。
- 模型安全边界：精读设置和 API 响应只能暴露模型 ID、提示词和状态，不能暴露 API Key。

### Task 1: 精读核心模块与配置

**Files:**
- Create: `easyread/deepread.py`
- Modify: `easyread/config.py`
- Test: `tests/test_deepread.py`

**Interfaces:**
- Produces `deepread.MAX_CHARS`, `deepread.DEFAULT_PROMPT`, `deepread.context(ws)`, `deepread.build_prompt(ws, custom_prompt)`, `deepread.limit(text)`, `deepread.read(ws)` and `deepread.generate(ws, cfg, cancel=None)` for later tasks.

- [x] **Step 1: Write failing tests**

  Add tests for: translated `zh` taking priority over `en`; English fallback when `zh` is empty; `limit` returning at most 5000 characters; `build_prompt` containing the custom prompt and fixed no-invention/length constraints; and `generate` writing `deepread.md` only after a successful model result.

- [x] **Step 2: Run the focused tests and verify RED**

  Run: `.venv/Scripts/python.exe -m unittest tests.test_deepread -v`

  Expected: failure because the deepread module and interfaces do not exist yet.

- [x] **Step 3: Implement the core module and defaults**

  Add the default prompt and 5000-character clamp. Build context from paper metadata and blocks, selecting translated text first and English text as fallback. Resolve the selected model with `chat_models.engine_cfg`, call `engines.run`, and atomically write `deepread.md` plus `deepread.json` only after success. Keep the previous Markdown file untouched on exceptions. Add `deepread.model` and `deepread.prompt` to config defaults.

- [x] **Step 4: Run the focused tests and verify GREEN**

  Run: `.venv/Scripts/python.exe -m unittest tests.test_deepread -v`

  Expected: all focused tests pass.

### Task 2: Persistent task execution and API

**Files:**
- Modify: `easyread/jobs.py`
- Modify: `easyread/server.py`
- Modify: `easyread/store.py` only if a shared state default is needed
- Test: `tests/test_deepread.py` and/or `tests/test_server.py` if a server test module is introduced

**Interfaces:**
- Consumes Task 1 `deepread.read` and `deepread.generate`.
- Produces `GET /api/p/{id}/deepread` and `POST /api/p/{id}/deepread`; POST returns the queued state and does not run unless explicitly called.

- [x] **Step 1: Write failing tests**

  Add coverage for POST-triggered queueing, duplicate queued/running requests returning the existing state, GET returning state/content, and the job path updating `deepread.json` to `done` or `error` without deleting an older Markdown file.

- [x] **Step 2: Run the focused tests and verify RED**

  Run the new focused test command.

  Expected: failure because the endpoints and `deepread` job branch do not exist yet.

- [x] **Step 3: Implement queue and endpoints**

  Add a dedicated `submit_deepread` path to the existing small-task queue. Persist queued/running/error/done state, deduplicate active jobs, and allow a manually queued job to resume after service restart without creating jobs for papers that never requested one. Add GET/POST handling under `/api/p/{id}/deepread`, protected by the existing X-Token rule for POST. Keep all state updates atomic and do not include keys in responses.

- [x] **Step 4: Run focused tests and verify GREEN**

  Run the focused deepread/server tests.

  Expected: all pass, including duplicate-trigger and old-document preservation cases.

### Task 3: Settings for model and prompt

**Files:**
- Create: `easyread/web/js/common/settings-deepread.js`
- Modify: `easyread/web/js/common/settings.js`
- Modify: `easyread/web/library.html`
- Modify: `easyread/web/reader.html`
- Test: JavaScript syntax checks and existing settings e2e coverage where available

**Interfaces:**
- Consumes `/api/config` and `/api/chat/models` data already loaded by settings.js.
- Produces `config.deepread.model` and `config.deepread.prompt` in the existing save request.

- [x] **Step 1: Add a frontend regression check first**

  Extend the existing browser test or add a lightweight DOM assertion that opening settings exposes a “精读论文” tab, a model selector, and a prompt textarea, and that saving sends the selected model and prompt fields.

- [x] **Step 2: Run the frontend check and verify RED**

  Run the relevant browser check or its documented fallback.

  Expected: failure because the tab and fields do not exist.

- [x] **Step 3: Implement the settings tab**

  Add the tab registration and render a model selector using the existing chat model list, with an option to follow the chat default, plus an editable default prompt and a clear 5000-character note. Extend settings collection/sync so switching tabs does not lose values and saving preserves the existing masked-Key behavior.

- [x] **Step 4: Run JavaScript checks and the frontend regression check**

  Run `node --check` for the changed scripts and the relevant browser test.

  Expected: syntax checks pass and the settings controls are present with the saved values.

### Task 4: Reader and library document workflow

**Files:**
- Create: `easyread/web/js/reader/deepread.js`
- Modify: `easyread/web/reader.html`
- Modify: `easyread/web/css/reader-panels.css` and/or a focused reader stylesheet
- Modify: `easyread/web/js/reader/pageview.js` for the additional side-panel state
- Modify: `easyread/web/js/reader/panels.js` for the toolbar action
- Modify: `easyread/web/js/library/detail.js` for paper-menu actions
- Modify: `easyread/web/js/library/app.js` only if the library refresh needs a summary field
- Test: JavaScript syntax checks and e2e/manual API workflow

**Interfaces:**
- Consumes Task 2 GET/POST deepread endpoints and Task 3 settings state.
- Produces a visible per-paper document panel with generate, regenerate, copy, download, loading, empty, done, and error states.

- [x] **Step 1: Add failing UI assertions**

  Add browser assertions for: no automatic POST on reader load; clicking the reader “精读” action opens the panel; clicking generate sends exactly one POST; done content renders; copy/download controls exist; and the library paper menu exposes the same manual trigger.

- [x] **Step 2: Run the UI checks and verify RED**

  Run the existing e2e command when Playwright is available; otherwise run the documented DOM/static checks and record the missing dependency.

  Expected: failure because the panel, action, and menu item do not exist.

- [x] **Step 3: Implement the reader/library workflow**

  Add a reader side panel backed by the existing side-panel width behavior. Render Markdown through the existing renderer, show status and character count, add manual generate/regenerate, copy, and download actions, and poll through the existing reader refresh cycle without auto-triggering. Add a library menu action that queues the current paper and refreshes its detail state.

- [x] **Step 4: Run UI syntax checks and the full test suite**

  Run all changed `node --check` commands, `.venv/Scripts/python.exe -m unittest discover -s tests -v`, and `git diff --check`.

  Expected: all unit tests and syntax checks pass; if Playwright is unavailable, report that specific gap instead of claiming full browser e2e coverage.

## Final Verification

- Run the full Python suite and all changed JavaScript syntax checks.
- Start/restart the local service and manually verify settings, manual trigger, status polling, and document rendering on one existing paper.
- Confirm `deepread.md` is never generated by simply opening a paper or completing translation.
- Inspect the final diff for accidental Key exposure and unrelated changes.
