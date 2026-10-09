// 页边 AI 讨论删除：检查前后端接线完整（按钮渲染、点击处理、API 端点、确认弹窗）。
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const margin = fs.readFileSync(path.join(root, "easyread/web/js/reader/margin.js"), "utf8");
const server = fs.readFileSync(path.join(root, "easyread/server.py"), "utf8");
const paperdata = fs.readFileSync(path.join(root, "easyread/paperdata.py"), "utf8");

function check(condition, message) {
  if (!condition) throw new Error(message);
}

// 前端：agent 卡片渲染删除按钮（只在有服务的在线版；离线导出没有服务，不能删）
check(margin.includes('data-a="del">删除</button>'), "agent 卡片要有删除按钮");
check(margin.includes('PR.store.mode === "server" ? \'<div class="acts"><button data-a="del">删除</button></div>\' : ""'),
  "删除按钮只在在线版渲染，离线导出不显示");

// 前端：点击走 deleteDiscussion，先确认再调接口，成功后更新本地状态并重渲染
check(margin.includes("PR.deleteDiscussion = async function"), "要有 PR.deleteDiscussion");
check(margin.includes('"/api/p/" + PR.pid + "/discuss/delete"'), "deleteDiscussion 要调删除接口");
check(margin.includes("await PR.confirm("), "删除前要弹确认框");
check(margin.includes("S.discussion.entries = (S.discussion.entries || []).filter((x) => x.id !== id)"), "成功后要从本地状态里去掉这条");
check(margin.includes("PR.renderMargin();"), "删除后要重渲染页边");
check(margin.includes("PR.applyMarks();"), "删除后要重刷正文下划线");
check(margin.includes('!nid && card.dataset.card && a && a.dataset.a === "del"'), "cardClick 要把 agent 卡片的删除按钮接到 deleteDiscussion");

// 后端：端点存在，走 paperdata.delete_discussion，带版本号返回
check(server.includes('action == "discuss" and len(parts) > 5 and parts[5] == "delete"'), "服务要有 /discuss/delete 端点");
check(server.includes("paperdata.delete_discussion(ws, did)"), "端点要调 delete_discussion");
check(server.includes('"versions": ws.versions()'), "返回要带版本号（页面轮询靠它对齐）");
check(paperdata.includes("def delete_discussion"), "paperdata 里要有 delete_discussion");

console.log("✓ 页边 AI 讨论删除：前后端接线完整（按钮 / 确认 / 接口 / 状态更新 / 重渲染）");

