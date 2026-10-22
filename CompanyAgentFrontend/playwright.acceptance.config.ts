import { defineConfig, devices } from '@playwright/test'

// These tests target built Nginx + actual api.main, not Vite or mocked routes.
// The documented Compose stack must already be running. Tests verify its Docker
// ownership and loopback port before sending data; no existing user server is reused.
export default defineConfig({
  testDir: './acceptance',
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 180_000,
  reporter: [['list'], ['html', { outputFolder: 'playwright-acceptance-report', open: 'never' }]],
  outputDir: 'test-results/acceptance',
  use: {
    baseURL: 'http://127.0.0.1:18874',
    trace: 'retain-on-failure',
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  },
  projects: [{ name: 'production-images', use: { ...devices['Desktop Chrome'] } }],
})
