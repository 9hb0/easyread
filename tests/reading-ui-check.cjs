const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const readerHtml = fs.readFileSync(path.join(root, "easyread/web/reader.html"), "utf8");
const readingJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/reading.js"), "utf8");
const anchorJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/anchor.js"), "utf8");
const panelsJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/panels.js"), "utf8");
const marginJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/margin.js"), "utf8");
const guideJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/guide.js"), "utf8");
const pageviewJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/pageview.js"), "utf8");
const baseCss = fs.readFileSync(path.join(root, "easyread/web/css/base.css"), "utf8");
const readerCss = fs.readFileSync(path.join(root, "easyread/web/css/reader.css"), "utf8");

function check(condition, message) {
  if (!condition) throw new Error(message);
}

check(readerHtml.includes("js/reader/reading.js"), "reader.html must load reading.js");
check(readerHtml.includes('data-act="reading"'), "toolbar must have the reading filter button");
check(readingJs.includes("PR.READING_CATS"), "reading.js must define categories centrally");
check(readingJs.includes("PR.applyReadingMarks") && readingJs.includes("PR.toggleReadingFilter"), "reading.js must render marks and expose the filter");
check(readingJs.includes("mark.sem"), "semantic marks must use the sem class");
check(anchorJs.includes("PR.textNodesIn"), "anchor.js must export the text-node walker for reuse");
check(panelsJs.includes('act === "reading"'), "toolbar click must open the reading filter");
check(marginJs.includes('src === "reading"'), "margin must render reading annotation cards");
check(marginJs.includes("card reading"), "reading cards must use the reading card class");
check(readingJs.includes("READING_ANN_TYPES") && readingJs.includes("runEnrich"), "reading.js must label annotation types and offer enrich");
check(readingJs.includes("/enrich"), "reading.js must call the enrich API");
for (const cat of ["background", "related_work", "problem", "method", "innovation", "evidence"]) {
  check(baseCss.includes("--sem-" + cat + ":"), "base.css must define --sem-" + cat);
  check(readerCss.includes("mark.sem.cat-" + cat), "reader.css must style mark.sem.cat-" + cat);
  check(readerCss.includes(".dot.cat-" + cat), "reader.css must style the ." + cat + " filter dot");
}
check(readerCss.includes(".card.reading"), "reader.css must style reading annotation cards");
check(readerHtml.includes("js/reader/guide.js") && readerHtml.includes('id="guidepanel"'), "reader.html must have the guide panel");
check(readerHtml.includes('data-act="guide"'), "toolbar must have the guide button");
check(guideJs.includes("PR.toggleGuide") && guideJs.includes("reading_guide"), "guide.js must render the research navigation");
check(guideJs.includes("data-goto"), "guide items must jump to source blocks");
check(pageviewJs.includes('name === "guide"'), "openSide must support the guide panel");
check(fs.readFileSync(path.join(root, "easyread/web/css/reader-panels.css"), "utf8").includes("#guidepanel"), "panel CSS must include #guidepanel");
console.log("reading UI checks passed");

