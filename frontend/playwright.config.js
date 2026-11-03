import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8893",
    channel: "chrome",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "python -m http.server 8893 --bind 127.0.0.1 --directory ../web",
    url: "http://127.0.0.1:8893/club-preview.html",
    reuseExistingServer: true,
  },
});
