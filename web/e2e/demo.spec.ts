import { expect, test } from "@playwright/test";

test("opens a Chinese real-run launcher with explicit system boundaries", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/");

  await expect(page).toHaveURL(/\/demo$/);
  await expect(page.getByRole("heading", { name: "AI 事故响应团队" })).toBeVisible();
  await expect(page.getByText("真实微服务与遥测 · 非预设答案")).toBeVisible();
  await expect(page.getByText("每次都会创建新的后端运行，不是历史结果回放。")).toBeVisible();
  await expect(page.getByRole("tab", { name: "粘贴告警" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "选择服务" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "上传告警文件" })).toBeVisible();
  await expect(page.getByRole("link", { name: "智能诊断" })).toHaveAttribute("aria-current", "page");
  expect(browserErrors).toEqual([]);
});
