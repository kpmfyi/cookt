import { defineConfig, devices } from "@playwright/test";

// Runs against the live server (scripts/demo.sh) or COOKT_URL.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: process.env.COOKT_URL ?? "http://127.0.0.1:8088" },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
