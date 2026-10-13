# AI 精读预处理

翻译只生成译文，不会自动调用精读模型。用户可从阅读页顶栏“标注”手动启动精读；默认重跑全部已译页，也可指定页码。

命令行精读：

    python -m easyread enrich PAPER_ID       # 重跑全部已译页
    python -m easyread enrich PAPER_ID --pages 3-5
    python -m easyread enrich PAPER_ID --all # 与默认行为相同，保留兼容

精读只写高亮、批注、研究要点和页面状态，不改已有译文。失败页面会进入 failed，译文仍保留，服务重启后任务会从 job.json 继续。每次默认重做全部已译页；服务重启恢复任务时只续跑尚未完成的页。

精读结果保存在 paper.json：

- 每个高亮绑定 block_id、类别、译文连续片段和 occurrence。
- 批注和研究要点带 source_refs，引用页码、文字块和原文短句都会校验。
- 页面状态保存 reading.version、zh_hash、content_hash 和 source_version。译文、公式、表格数值、原文块或来源版本变化后，旧标注自动失效。
- reading_guide.coverage 记录实际分析页、翻译页、目标范围和未纳入范围；范围不完整时不会显示全文完成。

原文分块缓存目前使用 schema 3：PDFium 产生的假空行按已核对的字符坐标还原段落，列表续行只在同栏、小幅缩进时合并。原文版本变化会使旧定位与精读失效；重新准备 PDF 后，使用 enrich 补处理即可，无须重译。已有缺字或不完整图注仍需单独修复原翻译。

job.log 记录每批高亮、批注和研究要点的校验错误数量。metrics.jsonl 只记录实际模型调用，包含翻译、手动精读、局部修复和综合的耗时与引擎上报用量。Codex CLI 通过 JSONL 的 turn.completed 统计 Token；未上报的用量保留为空，不当作零成本。余额不足会停止后续尚未启动的批次。

浏览器端使用原生文本节点定位高亮，公式节点、列表项边界和不连续 Markdown 片段不会被包裹。高亮筛选只影响显示；点击高亮可查看批注依据和原文页跳转。

## 八类高亮与阅读密度（2026-10-13）

原六类保持顺序，新增 `finding`（关键结论，已有实验观察或实际结果）和 `limitation`（局限性，只标作者明确承认的限制）。实验设计归 `evidence`，实验发现归 `finding`；普通方法描述与实质创新分别归 `method` 和 `innovation`。研究导航同步提供“关键结论”分组。

高亮可选保存 `importance`（整数1–5）和 `reason`（去首尾空格，最多120字）。模型自由提供的 id、颜色、位置和来源不作为高亮存储字段；仍逐字校验 `block_id + quote + occurrence`。非法评分/理由只丢弃该字段并记录问题；非法高亮只丢弃该条，合法标注保留。批注和研究要点的英文来源仍严格校验，失败页保留原有一次专项修复。

“标注”面板可选择精简、标准、深度：有评分的新候选分别显示5分、至少4分、至少2分；无评分的旧候选各档继续显示，并提示未评级。分类统计区分当前显示数和全部有效候选数。类别、密度、总开关仅改变浏览器显示，不调用模型、不删除 `paper.json` 中的候选。点击标注可查看真实保存的评分、理由、批注及原文。

跨页质控只合并同类别、去空白后完全一致且不少于12字的原话，不根据相似度猜测语义。较高评分优先，同分选择正文，再按文档顺序。`reading.quality` 保存可重建的版本、输入哈希和抑制键，不依赖可能变化的高亮序号id。译文、公式、原文版本、页状态、候选或评分改变时，前端忽略过时缓存。重新精读会在本地重建；“去重复”默认关闭，人工质量确认前按试验功能使用，开启后仍可显示被抑制项。

数据兼容版本仍为 `READING_VERSION=2`，新生成页另记 `prompt_version=1`，派生质控 `quality.version=1`。旧论文无需重译，也不会自动补写评分；重新手动精读才生成新分类和评级。

本次程序回归和缓存论文对照见 [本轮验收记录](reading-acceptance.md)。人工必要性/类别准确率、核心召回及新提示词同模型A/B仍须独立检查，不能由候选数量减少代替。

## 验证

    .venv\Scripts\python.exe -m unittest discover -s tests
    node tests\reading-browser.cjs
    node tests\e2e.cjs
    python tests\reading_real.py prepare
    python tests\reading_real.py enrich
    node tests\reading_screenshots.cjs http://127.0.0.1:PORT PAPER_ID

端到端和真实页面截图脚本需安装 Playwright；也可用 PLAYWRIGHT 环境变量指向已有的模块目录。浏览器精读测试使用 PLAYWRIGHT_MODULE 和 READING_BROWSER_EXE 指定模块及浏览器。

reading_real.py 会把论文复制到被 .gitignore 忽略的 artifacts/reading-qa，不会修改 library/ 中的原论文。真实模型的调用、耗时、token 用量写入每篇副本的 metrics.jsonl；模型额度或网络错误会记录在 enrich-results.json，不能把失败页算作质量通过。

使用 enrich --all 重跑全部选定样本，ab 在相同模型与页面上比较译文和精读标注。可通过临时 EASYREAD_HOME 指定测试配置。运行真实测试会调用已配置的模型；自动化发现测试不会调用模型。

验收记录、样例与剩余限制见 [精读验收报告](reading-acceptance.md)。
