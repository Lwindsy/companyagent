import { defineConfig, devices } from '@playwright/test'

// Keep real local HTTP checks separate from the existing response-mocked suite.
// One worker makes fixture-wide extraction counters comparable within each case.
// Both services are owned by this run; occupied ports fail instead of reusing an
// unrelated development/production process. No external model endpoint is used.
export default defineConfig({
  testDir: './integration',
  testMatch: '**/*.spec.ts',
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: 'playwright-integration-report', open: 'never' }]],
  outputDir: 'test-results/integration',
  use: {
    baseURL: 'http://127.0.0.1:5174',
    trace: 'retain-on-failure',
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  },
  projects: [{ name: 'business-http', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'node integration/servers.mjs api',
      url: 'http://127.0.0.1:8873/health',
      reuseExistingServer: false,
    },
    {
      command: 'node integration/servers.mjs web',
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: false,
    },
  ],
})
