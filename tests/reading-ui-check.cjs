const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const readerHtml = fs.readFileSync(path.join(root, "easyread/web/reader.html"), "utf8");
const readingJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/reading.js"), "utf8");
const anchorJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/anchor.js"), "utf8");
const panelsJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/panels.js"), "utf8");
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
for (const cat of ["background", "related_work", "problem", "method", "innovation", "evidence"]) {
  check(baseCss.includes("--sem-" + cat + ":"), "base.css must define --sem-" + cat);
  check(readerCss.includes("mark.sem.cat-" + cat), "reader.css must style mark.sem.cat-" + cat);
  check(readerCss.includes(".dot.cat-" + cat), "reader.css must style the ." + cat + " filter dot");
}
console.log("reading UI checks passed");

