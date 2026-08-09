import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e-live",
  timeout: 300_000,
  expect: { timeout: 30_000 },
  reporter: "line",
  outputDir: "../.runtime/playwright-live",
  use: {
    baseURL: "http://127.0.0.1:5180",
    browserName: "chromium",
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 960 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
