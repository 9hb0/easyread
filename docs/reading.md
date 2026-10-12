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

浏览器端使用原生文本节点定位高亮，公式节点、列表项边界和不连续 Markdown 片段不会被包裹。高亮筛选只影响显示；点击高亮可查看批注依据和原文页跳转。

## 验证

    .venv\Scripts\python.exe -m unittest discover -s tests
    node tests\reading-browser.cjs
    node tests\e2e.cjs
    python tests\reading_real.py prepare
    python tests\reading_real.py enrich
    python tests\reading_screenshots.cjs http://127.0.0.1:PORT PAPER_ID

reading_real.py 会把论文复制到被 .gitignore 忽略的 artifacts/reading-qa，不会修改 library/ 中的原论文。真实模型的调用、耗时、token 用量写入每篇副本的 metrics.jsonl；模型额度或网络错误会记录在 enrich-results.json，不能把失败页算作质量通过。
