import { expect, test } from "@playwright/test";

test("effect validation is a dense single-screen proof workspace", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/evaluations");
  await expect(page.getByRole("heading", { name: "不是“看起来正确”，而是被固定规则验证" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "三次独立 seed" })).toBeVisible();
  await expect(page.getByText("seed 64")).toBeVisible();
  await expect(page.getByText("seed 71")).toBeVisible();
  await expect(page.getByText("seed 79")).toBeVisible();
  await expect(page.getByText("8,449 µUSD")).toBeVisible();
  const dashboard = await page.locator(".proof-dashboard").boundingBox();
  expect((dashboard?.y ?? 0) + (dashboard?.height ?? 0)).toBeLessThanOrEqual(960);
  await page.screenshot({ path: "../.runtime/effect-proof-v2.png", fullPage: false });
  expect(errors).toEqual([]);
});

test("governed evolution exposes the diff and rejection evidence without a drawer", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/evolution");
  await expect(page.getByRole("heading", { name: "系统会提出改进，但没有权力偷偷上线" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "线上 Active 保持不变" })).toBeVisible();
  await expect(page.getByText("Prompt Diff")).toBeVisible();
  await expect(page.getByText("验证集根因准确率")).toBeVisible();
  await expect(page.getByText("退化 25%")).toBeVisible();
  await expect(page.getByText("根因回归阈值")).toBeVisible();
  const dashboard = await page.locator(".governance-dashboard").boundingBox();
  expect((dashboard?.y ?? 0) + (dashboard?.height ?? 0)).toBeLessThanOrEqual(960);
  await page.screenshot({ path: "../.runtime/governance-v2.png", fullPage: false });
  expect(errors).toEqual([]);
});
