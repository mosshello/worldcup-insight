import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  testMatch: "model-workspace.spec.js",
  workers: 1,
  timeout: 120000,
  expect: { timeout: 15000 },
  metadata: { previewPath: "/index.html" },
  use: {
    baseURL: "http://127.0.0.1:8897",
    channel: process.env.PW_EXECUTABLE ? undefined : "chrome",
    launchOptions: process.env.PW_EXECUTABLE
      ? { executablePath: process.env.PW_EXECUTABLE }
      : {},
    headless: true,
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command:
      "node scripts/serve-check.mjs",
    url: "http://127.0.0.1:8897",
    reuseExistingServer: false,
  },
});
