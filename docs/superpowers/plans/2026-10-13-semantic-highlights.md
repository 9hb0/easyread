# 语义高亮修改实施计划

> 执行：依照用户提供的实施规范直接推进；采用测试先行，并行处理互不重叠的文件，最后独立复核。

**Goal:** 完成八类高亮、可选评分/理由、三档密度、可重建的跨页去重及兼容验收。

**Architecture:** 保留 Python 服务与原生 JS。合法候选保存在块内，质控结果为独立派生缓存，前端仅过滤显示。

**Tech Stack:** Python unittest、原生 JS、Playwright。

**Spec:** `D:/Desktop/EasyRead_AI语义高亮修改实施与验收规范_v1.0.md`

## 全局约束

- READING_VERSION=2；锚点仍为 block_id + quote + occurrence。
- 保留现有未提交的手动精读流程，不恢复翻译自动精读。
- 不改译文、人工笔记、公式、原文定位或来源验证边界。
- 不增加模型调用；跨批上下文最多16条、2400字符。
- 缺评分的旧候选各档保留；lite=5、standard>=4、deep>=2。
- 质量只抑制同类别、规范化空白后完全相同的完整原话；不做近义推断。
- 未完成人工质量验收，去重复默认关闭，可开启试验；提供显示被抑制项的开关。

## 风险检查

- 缓存必须随候选、评分、译文、源版本、页状态变化失效。
- 存储的来源引用不能由模型伪造；仅可恢复高亮错误不让整页失败。
- 模式切换不能写 JSON 或发出精读网络请求。
- 同句不同条件、数字、否定、类别不得误合并。
- 旧流程测试改为对手动精读路径进行同等保护验证，不能删掉保护断言。

## Task 1: 后端分类、字段和提示词

Files: easyread/reading.py、easyread/prompts.py、tests/test_reading_semantics.py。
Produces: reading.PROMPT_VERSION=1、normalize_importance(value)->int|None；finding insights及findings导航组。

- [x] 新增八类、非法评分、可选理由、白名单和有界跨批上下文测试并观察失败。
- [x] 实现并跑相关单元测试；记录 prompt_version，保持旧页面兼容。

## Task 2: 前端密度与弹窗

Files: reading.js、guide.js、base.css、reader.css、reading-browser.cjs、reading-ui-check.cjs。
Consumes: reading.quality={version:1,input_hash,suppressed:[{key,canonical_key,reason}]}。
Produces: shouldShowHighlight、density筛选、有效候选与可见统计、评分/理由弹窗。

- [x] 浏览器先覆盖三档、旧候选、八类、零网络请求和缓存失效并观察失败。
- [x] 实现纯显示筛选与质控开关，验证暗色、移动端、键盘、原文跳转。

## Task 3: 派生质控与错误隔离

Files: reading_quality.py、translate.py、tests/test_reading_quality.py、tests/test_reading_quality_pipeline.py。
Interfaces: highlight_stable_key(block_id,h)、quality_input_hash(paper)、rebuild_quality(paper)。

- [x] 先测确定性、幂等、候选不变、缓存失效、评分优先上限及单条错误隔离。
- [x] 在精读写回和收尾重建质量，不使用模型；来源错误继续专项修复。
- [x] 修正已有手动精读改动的回归测试，纯翻译设置继续有效。

## Task 4: 综合验收及交付

- [x] 运行全量Python和浏览器测试，记录本次日志与截图。
- [x] 在隔离的真实论文JSON副本上比较派生去重；准备人工复核表，不冒充人工质量达标。
- [x] 独立审查核心差异并修复；更新reading文档、验收记录和CHANGELOG。

## 执行记录

- 起始HEAD：2a4e321f241d966518fe045d6865af4f5ae608d7。
- 工作分支：codex/semantic-highlights。
- 原改动与library JSON备份：C:/Users/Axiaoxin/AppData/Local/Temp/easyread-semantic-20261013-182434。
- 基线：artifacts/reading-qa/semantic-baseline.log，已有手动精读与旧测试断言冲突。
- 决定：不提交用户既有未提交改动；代码和验收结果保留在工作分支供审阅。
