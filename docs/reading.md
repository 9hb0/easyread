# AI 精读预处理

翻译任务默认在同一批模型响应中生成译文和精读数据。精读可以通过配置里的 reading_enabled 关闭；关闭后仍走原有纯翻译流程。

旧论文可以直接补精读：

    python -m easyread enrich PAPER_ID
    python -m easyread enrich PAPER_ID --pages 3-5
    python -m easyread enrich PAPER_ID --all

补精读只写高亮、批注、研究要点和页面状态，不改已有译文。失败页面会进入 failed，译文仍保留，服务重启后任务会从 job.json 继续。

精读结果保存在 paper.json：

- 每个高亮绑定 block_id、类别、译文连续片段和 occurrence。
- 批注和研究要点带 source_refs，引用页码、文字块和原文短句都会校验。
- 页面状态保存 reading.version、zh_hash、content_hash 和 source_version。译文、公式、表格数值、原文块或来源版本变化后，旧标注自动失效。
- reading_guide.coverage 记录实际分析页、翻译页、目标范围和未纳入范围；范围不完整时不会显示全文完成。

原文分块缓存目前使用 schema 3：PDFium 产生的假空行按已核对的字符坐标还原段落，列表续行只在同栏、小幅缩进时合并。原文版本变化会使旧定位与精读失效；重新准备 PDF 后，使用 enrich 补处理即可，无须重译。已有缺字或不完整图注仍需单独修复原翻译。

job.log 记录每批高亮、批注和研究要点的校验错误数量。metrics.jsonl 只记录实际模型调用，包含正常翻译、补精读、局部修复和综合的耗时与引擎上报用量。Codex CLI 通过 JSONL 的 turn.completed 统计 Token；未上报的用量保留为空，不当作零成本。余额不足会停止后续尚未启动的批次。

浏览器端使用原生文本节点定位高亮，公式节点、列表项边界和不连续 Markdown 片段不会被包裹。高亮筛选只影响显示；点击高亮可查看批注依据和原文页跳转。

## 验证

    .venv\Scripts\python.exe -m unittest discover -s tests
    node tests\reading-browser.cjs
    node tests\e2e.cjs
    python tests\reading_real.py prepare
    python tests\reading_real.py enrich
    node tests\reading_screenshots.cjs http://127.0.0.1:PORT PAPER_ID

端到端和真实页面截图脚本需安装 Playwright；也可用 PLAYWRIGHT 环境变量指向已有的模块目录。浏览器精读测试使用 PLAYWRIGHT_MODULE 和 READING_BROWSER_EXE 指定模块及浏览器。

reading_real.py 会把论文复制到被 .gitignore 忽略的 artifacts/reading-qa，不会修改 library/ 中的原论文。真实模型的调用、耗时、token 用量写入每篇副本的 metrics.jsonl；模型额度或网络错误会记录在 enrich-results.json，不能把失败页算作质量通过。

使用 enrich --all 复测全部选定样本，ab 在相同模型与页面上比较纯翻译和精读。可通过临时 EASYREAD_HOME 指定测试配置。运行真实测试会调用已配置的模型；自动化发现测试不会调用模型。

验收记录、样例与剩余限制见 [精读验收报告](reading-acceptance.md)。
