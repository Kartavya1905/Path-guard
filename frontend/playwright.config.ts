import { defineConfig } from '@playwright/test'

// Smoke tests drive the real UI against the real API (Compose publishes it on 8600).
// PW_BASE_URL=http://127.0.0.1:3600 tests the Compose build instead of the Vite dev server.
// On Windows the installed Microsoft Edge is used, so no browser download is needed.
// Elsewhere run `npx playwright install chromium` once, or set PW_CHANNEL.
const baseURL = process.env.PW_BASE_URL ?? 'http://127.0.0.1:5600'
const channel = process.env.PW_CHANNEL ?? (process.platform === 'win32' ? 'msedge' : undefined)

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: { baseURL, channel, trace: 'retain-on-failure' },
  webServer: process.env.PW_BASE_URL
    ? undefined
    : { command: 'npm run dev', url: baseURL, reuseExistingServer: true, timeout: 60_000 },
})
