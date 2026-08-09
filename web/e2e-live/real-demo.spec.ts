import { expect, test } from "@playwright/test";

test("a new real demo run reaches approval, executes rollback and verifies recovery", async ({ page }) => {
  test.setTimeout(600_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "AI 事故响应团队" })).toBeVisible();
  await expect(page.getByText("每次都会创建新的后端运行，不是历史结果回放。")).toBeVisible();
  await expect(page.getByText("每次审批", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "启动真实案例" }).click();

  await expect(page).toHaveURL(/\/incidents\/inc_/);
  await expect(page.getByRole("heading", { name: "AI 团队正在工作" })).toBeVisible();
  await expect(page.getByText(/正在准备隔离的真实微服务故障环境|受控故障已生效/)).toBeVisible();
  await expect(page.getByText(/分诊 Agent 正在确定影响范围|已锁定 .* 个相关服务/).first()).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText(/事故指挥 Agent 正在交叉验证|多路证据已经汇聚/).first()).toBeVisible({ timeout: 260_000 });
  await expect(page.getByText("等待你的决定")).toBeVisible({ timeout: 360_000 });
  await expect(page.getByText("最可能根因")).toBeVisible();
  await expect(page.getByText("签名审批 · 单次 nonce · 幂等执行")).toBeVisible();

  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "批准并执行" }).click();
  await expect(page.getByText("已验证恢复", { exact: true }).first()).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText("Prometheus SLO 已通过")).toBeVisible();
  await expect(page.getByText("本次诊断、审批、执行与恢复轨迹已进入受控离线样本池")).toBeVisible();
  await page.screenshot({ path: "../.runtime/live-command-resolved.png", fullPage: false });

  expect(errors).toEqual([]);
});
