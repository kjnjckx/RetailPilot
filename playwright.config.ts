import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser",
  workers: 1,
  timeout: 60000,
  use: {
    headless: true,
    launchOptions: {
      executablePath: process.env.RETAILPILOT_BROWSER_EXECUTABLE,
    },
    viewport: { width: 1440, height: 1000 },
  },
  reporter: "list",
});
