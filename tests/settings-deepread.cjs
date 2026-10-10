const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const settings = fs.readFileSync(path.join(root, "easyread/web/js/common/settings.js"), "utf8");
const tab = fs.readFileSync(path.join(root, "easyread/web/js/common/settings-deepread.js"), "utf8");
const library = fs.readFileSync(path.join(root, "easyread/web/library.html"), "utf8");
const reader = fs.readFileSync(path.join(root, "easyread/web/reader.html"), "utf8");

function check(condition, message) {
  if (!condition) throw new Error(message);
}

check(settings.includes('"deepread"'), "settings must register the deepread tab");
check(settings.includes("deepread: { model:") && settings.includes("prompt:"), "settings save must include deepread fields");
check(tab.includes("deepread ="), "deepread settings tab is missing");
check(tab.includes('data-k="deepread.model"') && tab.includes('data-k="deepread.prompt"'), "deepread model and prompt controls are missing");
check(tab.includes('data-k="wiki.vault"') && settings.includes("wiki: { vault:"), "wiki vault setting is missing from save");
check(library.includes("settings-deepread.js") && reader.includes("settings-deepread.js"), "both pages must load deepread settings");
console.log("settings deepread checks passed");
