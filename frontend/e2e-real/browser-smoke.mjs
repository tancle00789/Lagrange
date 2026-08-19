// Real-stack browser smoke: drives the actual UI against the running compose
// stack (no route mocking). Usage:
//   cd frontend && node e2e-real/browser-smoke.mjs [baseURL] [screenshotDir]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const BASE = process.argv[2] ?? "http://localhost:8000";
const SHOTS = process.argv[3] ?? "e2e-real/shots";
const TOKEN = "dev:tenant-a:user-1:admin";
const QUESTION =
  "What are the main approaches to KV cache compression for efficient LLM inference?";
const TIMEOUT_MS = 12 * 60 * 1000;

mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => page.screenshot({ path: join(SHOTS, name), fullPage: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(30_000);

try {
  await page.goto(BASE);
  await page.getByPlaceholder("访问令牌").fill(TOKEN);
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByRole("heading", { name: "新建任务" }).waitFor();
  console.log("01 authenticated");
  await shot(page, "01-tasks.png");

  await page.getByPlaceholder("提出一个研究问题……").fill(QUESTION);
  await page.getByLabel(/允许外部文献检索/).check();
  await page.getByRole("button", { name: "创建任务" }).click();
  console.log("02 task created");
  await page.waitForTimeout(3000);
  await shot(page, "02-task-running.png");

  // Wait for the detail panel's status badge to reach a terminal state.
  const statusBadge = page.locator(".detail .status").first();
  const started = Date.now();
  for (;;) {
    const status = (await statusBadge.textContent())?.trim();
    if (status === "completed" || status === "failed" || status === "cancelled") {
      if (status !== "completed") throw new Error(`task ended with status: ${status}`);
      break;
    }
    if (Date.now() - started > TIMEOUT_MS) throw new Error("task did not complete in time");
    await page.waitForTimeout(5000);
  }
  console.log(`03 task completed in ${Math.round((Date.now() - started) / 1000)}s`);
  await page.waitForTimeout(1500); // let report/events panels render
  await shot(page, "03-task-completed.png");

  const bodyText = await page.locator(".detail").innerText();
  const hasAnswer = bodyText.length > 200 && !/Waiting for events/.test(bodyText);
  console.log(`04 detail rendered: ${hasAnswer ? "answer + timeline present" : "THIN"}`);

  await page.getByRole("button", { name: "技能", exact: true }).click();
  await page.waitForTimeout(2000);
  await shot(page, "04-skills-panel.png");
  const skillsText = await page.locator(".detail").innerText();
  console.log(
    `05 skills panel: approved section ${/已通过 \([1-9]/.test(skillsText) ? "non-empty" : "empty"}`,
  );

  console.log("BROWSER SMOKE OK");
} finally {
  await browser.close();
}
