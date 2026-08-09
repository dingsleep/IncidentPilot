import { expect, test } from "@playwright/test";

test("renders the governed evolution ledger without automatic promotion", async ({ page }) => {
  await page.goto("/evolution");

  await expect(page.getByRole("heading", { name: "会学习，但不能偷偷改变自己" })).toBeVisible();
  await expect(page.getByText(/线上 Agent 没有自修改权限/)).toBeVisible();
  await expect(page.getByRole("button", { name: /promote/i })).toHaveCount(0);
});
