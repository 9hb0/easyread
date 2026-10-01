const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const readerHtml = fs.readFileSync(path.join(root, "easyread/web/reader.html"), "utf8");
const readerJs = fs.readFileSync(path.join(root, "easyread/web/js/reader/deepread.js"), "utf8");
const panels = fs.readFileSync(path.join(root, "easyread/web/js/reader/panels.js"), "utf8");
const detail = fs.readFileSync(path.join(root, "easyread/web/js/library/detail.js"), "utf8");
const panelsCss = fs.readFileSync(path.join(root, "easyread/web/css/reader-panels.css"), "utf8");

function check(condition, message) {
  if (!condition) throw new Error(message);
}

check(readerHtml.includes('id="deepreadpanel"') && readerHtml.includes('data-act="deepread"'), "reader deepread panel/action is missing");
check(readerJs.includes("PR.toggleDeepRead") && readerJs.includes('data-dr="generate"'), "reader deepread controls are missing");
check(readerJs.includes("/deepread") && !readerJs.includes("toggleDeepRead();"), "deepread must not auto-trigger on load");
check(panels.includes('act === "deepread"'), "reader toolbar does not open deepread");
check(detail.includes("deepread") && detail.includes("生成精读"), "library menu lacks deepread action");
check(panelsCss.includes("#notespanel, #chatpanel, #deepreadpanel") && panelsCss.includes("#pageview, #deepreadpanel"), "deepread panel lacks narrow-screen width rules");
console.log("deepread UI checks passed");
