import { expect, test } from "@playwright/test";

test("a completed real run fits the command center and exposes technical evidence", async ({ page, request }) => {
  const response = await request.get("http://127.0.0.1:8201/api/v1/incidents?limit=10", {
    headers: { "X-IncidentPilot-Actor": "local-viewer" },
  });
  const body = await response.json() as { items: Array<{ id: string; status: string }> };
  const incident = body.items.find((item) => item.status === "RESOLVED")
    ?? body.items.find((item) => item.status === "RESOLVED_READ_ONLY");
  expect(incident).toBeTruthy();

  await page.goto(`/incidents/${incident?.id}`);
  await expect(page.locator(".compact-command-page")).toBeVisible();
  await expect(page.getByLabel("诊断结论与处置")).toBeVisible();
  await expect(page.getByText("最可能根因")).toBeVisible();
  await expect(page.getByText("建议动作")).toBeVisible();
  if (incident?.status === "RESOLVED") {
    await expect(page.getByText("Prometheus SLO 已通过")).toBeVisible();
    await expect(page.getByText("已验证恢复", { exact: true }).first()).toBeVisible();
  }

  const surface = await page.locator(".command-surface").boundingBox();
  expect(surface).toBeTruthy();
  expect((surface?.y ?? 0) + (surface?.height ?? 0)).toBeLessThanOrEqual(960);
  await page.screenshot({ path: "../.runtime/live-command-completed.png", fullPage: false });

  await page.getByRole("button", { name: "专业详情 ↗" }).click();
  await expect(page.getByRole("heading", { name: "运行证据与工程控制" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Evidence/ })).toBeVisible();
  await page.getByRole("button", { name: /Evidence/ }).click();
  expect(await page.locator(".drawer-evidence article").count()).toBeGreaterThanOrEqual(3);
  await page.screenshot({ path: "../.runtime/professional-drawer-v2.png", fullPage: false });
});

test("a read-only run clearly ends and offers real next actions", async ({ page, request }) => {
  const response = await request.get("http://127.0.0.1:8201/api/v1/incidents?limit=100", {
    headers: { "X-IncidentPilot-Actor": "local-viewer" },
  });
  const body = await response.json() as { items: Array<{ id: string; status: string }> };
  const incident = body.items.find((item) => item.status === "RESOLVED_READ_ONLY");
  expect(incident).toBeTruthy();

  await page.goto(`/incidents/${incident?.id}`);
  await expect(page.getByText("本次只读诊断已完成")).toBeVisible();
  await expect(page.getByText("这里不是卡住，也不需要继续审批")).toBeVisible();
  await expect(page.getByRole("link", { name: "发起新的真实诊断" })).toHaveAttribute("href", "/demo");
  await expect(page.getByRole("link", { name: "查看事故记录" })).toHaveAttribute("href", "/incidents");
  await page.screenshot({ path: "../.runtime/read-only-terminal-v2.png", fullPage: false });
});
