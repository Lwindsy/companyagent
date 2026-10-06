import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

async function mockServices(page: Page) {
  await page.route(/\/api\/(python|java|dotnet)\//, async (route) => {
    const path = new URL(route.request().url()).pathname
    const result = path.endsWith('/chat')
      ? {
          conv_id: 'test-conversation',
          response:
            '## A clear next step\n\nHere is your **refund guidance**.\n\n- Check your order\n- Contact the billing team',
          agent_type: 'billing',
          knowledge_used: true,
        }
      : path.endsWith('/admin/login')
        ? { access_token: 'fixture-token', username: 'operator' }
        : path.endsWith('/health')
          ? { status: 'ok' }
          : path.endsWith('/knowledge/stats')
            ? { total_chunks: 128 }
            : path.endsWith('/admin/overview')
              ? { knowledge_chunks: 128, alerts: [] }
              : path.endsWith('/monitor')
                ? { status: 'ok', requests: 42, agents: { general: { success_rate: 0.98 } } }
                : path.endsWith('/search')
                  ? {
                      results: [
                        {
                          id: 'policy',
                          title: 'Refund policy',
                          content: 'Refund reviews take 3–5 business days.',
                          score: 0.87,
                        },
                      ],
                    }
                  : { added_chunks: 1, total_chunks: 129 }
    await route.fulfill({ json: result })
  })
}

async function signIn(page: Page) {
  await page.getByRole('button', { name: 'Staff sign in' }).click()
  await page.getByLabel('Username', { exact: true }).fill('operator')
  await page.getByLabel('Password', { exact: true }).fill('fixture-password')
  await page.getByRole('button', { name: 'Enter workspace' }).click()
  await expect(page.getByText('Healthy', { exact: true })).toBeVisible()
}

test('chat, safe markdown, persistence, guides, deletion, and theme', async ({ page }) => {
  await mockServices(page)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Big questions. Clear next steps.' })).toBeVisible()
  await page.getByRole('button', { name: /Billing & refunds/ }).click()
  await expect(page.getByRole('textbox', { name: 'Message support' })).toHaveValue(
    'I need help with a refund or billing issue.',
  )
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'A clear next step' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'A clear next step' })).toBeVisible()
  await page.getByRole('button', { name: 'Knowledge & guides' }).click()
  await expect(page.getByRole('heading', { name: 'How support requests are handled' })).toBeVisible()
  await page.getByRole('tab', { name: /Technical highlights/ }).click()
  await page.getByRole('textbox', { name: 'Search this guide' }).fill('zzzznotfound')
  await expect(page.getByRole('heading', { name: 'No chapters found.' })).toBeVisible()
  await page.getByRole('button', { name: 'Back to your conversation' }).click()
  await expect(page.getByRole('heading', { name: 'A clear next step' })).toBeVisible()
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: /^Delete I need help/ }).click()
  await page.getByRole('button', { name: 'Delete conversation', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Big questions. Clear next steps.' })).toBeVisible()
  expect(errors).toEqual([])
})

test('admin metrics, knowledge search, import, backend session isolation, and logout', async ({ page }) => {
  await mockServices(page)
  await page.goto('/')
  await signIn(page)
  await expect(page.getByText('128', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: 'Search support knowledge' }).fill('refund')
  await page.getByRole('button', { name: 'Search knowledge', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Refund policy', exact: true })).toBeVisible()
  await page.getByLabel('Document title').fill('Test policy')
  await page.getByLabel('Content', { exact: true }).fill('Test policy content.')
  await page.getByRole('button', { name: 'Add document', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('was added')
  await page.getByLabel('Upload knowledge file').setInputFiles({
    name: 'policy.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# Policy\nKnowledge content.'),
  })
  await expect(page.getByRole('status')).toContainText('uploaded successfully')
  await page.getByRole('button', { name: 'Connection settings' }).click()
  await page.getByLabel('Python API').fill('javascript:bad')
  await page.getByRole('button', { name: 'Save settings' }).click()
  await expect(page.getByRole('alert')).toContainText('relative API path')
  await page.getByRole('button', { name: /Java Spring Boot/ }).click()
  await expect(page.getByRole('heading', { name: 'A fresh connection.' })).toBeVisible()
  await page.getByRole('button', { name: /Python FastAPI/ }).click()
  await expect(page.getByText('Healthy', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  expect(await page.evaluate(() => sessionStorage.getItem('support.admin.sessions.v2'))).toBe('{}')
})

test('untrusted markdown does not inject HTML, execute links, or load remote images', async ({ page }) => {
  await mockServices(page)
  await page.route('**/api/python/chat', (route) =>
    route.fulfill({
      json: {
        response:
          '<script>window.compromised=true</script>\n\n[unsafe](javascript:alert(1))\n\n![tracking](https://untrusted.test/pixel.png)\n\n**Still readable**',
        conv_id: 'safe',
      },
    }),
  )
  await page.goto('/')
  await page.getByRole('textbox', { name: 'Message support' }).fill('Hello')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(page.getByText('Still readable', { exact: true })).toBeVisible()
  await expect(page.locator('.markdown script')).toHaveCount(0)
  await expect(page.locator('.markdown img')).toHaveCount(0)
  await expect(page.locator('.markdown a[href^="javascript:"]')).toHaveCount(0)
})

test('expired admin sessions return to login and clear the current token', async ({ page }) => {
  await mockServices(page)
  await page.goto('/')
  await signIn(page)
  await page.route('**/api/python/search?**', (route) =>
    route.fulfill({ status: 401, json: { detail: 'Administrator session has expired' } }),
  )
  await page.getByRole('textbox', { name: 'Search support knowledge' }).fill('refund')
  await page.getByRole('button', { name: 'Search knowledge', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Staff sign in' })).toBeVisible()
  await expect(page.getByText('Your administrator session has expired. Please sign in again.')).toBeVisible()
  expect(await page.evaluate(() => sessionStorage.getItem('support.admin.sessions.v2'))).toBe('{}')
})

test('partial monitoring failure preserves successful health and knowledge metrics', async ({ page }) => {
  await mockServices(page)
  await page.route('**/api/python/monitor', (route) =>
    route.fulfill({ status: 503, json: { detail: 'Monitor offline' } }),
  )
  await page.goto('/')
  await signIn(page)
  await expect(page.getByText('128', { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('Monitor offline')
})

test('keyboard navigation, mobile menu and responsive layouts', async ({ page }) => {
  await mockServices(page)
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 })
    await page.goto('/')
    await expect(page.getByRole('textbox', { name: 'Message support' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    if (width === 375) {
      await page.getByRole('button', { name: 'Open navigation' }).click()
      await page.getByRole('button', { name: 'Knowledge & guides' }).click()
      await expect(page.getByRole('heading', { name: 'How support requests are handled' })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    }
  }
  await page.keyboard.press('Control+k')
  await expect(page.getByRole('dialog', { name: 'Quick navigation' })).toBeVisible()
  await page.getByRole('textbox', { name: 'Find a page or conversation' }).fill('new conversation')
  await page.getByRole('dialog').getByRole('button', { name: 'New conversation' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('visual review: desktop, dark theme, mobile, guide, and admin screenshots', async ({ page }) => {
  await mockServices(page)
  await page.setViewportSize({ width: 1440, height: 1080 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Big questions. Clear next steps.' })).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({
    path: 'test-results/visual/support-desktop.png',
    fullPage: true,
    animations: 'disabled',
  })
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await page.screenshot({
    path: 'test-results/visual/support-dark.png',
    fullPage: true,
    animations: 'disabled',
  })
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await signIn(page)
  await page.screenshot({
    path: 'test-results/visual/admin-desktop.png',
    fullPage: true,
    animations: 'disabled',
  })
  await page.getByRole('button', { name: 'Knowledge & guides' }).click()
  await expect(page.getByRole('heading', { name: 'How support requests are handled' })).toBeVisible()
  await page.screenshot({
    path: 'test-results/visual/guides-desktop.png',
    fullPage: true,
    animations: 'disabled',
  })
  await page.setViewportSize({ width: 375, height: 900 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Big questions. Clear next steps.' })).toBeVisible()
  await page.screenshot({
    path: 'test-results/visual/support-mobile.png',
    fullPage: true,
    animations: 'disabled',
  })
})

test('key screens meet automated WCAG A/AA checks', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await mockServices(page)
  await page.goto('/')
  await expect(page.getByRole('textbox', { name: 'Message support' })).toBeVisible()
  const scan = async () => {
    const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    expect(
      result.violations.map((item) => ({
        id: item.id,
        nodes: item.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })),
      })),
    ).toEqual([])
  }
  await scan()
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await scan()
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await signIn(page)
  await scan()
  await page.getByRole('button', { name: 'Knowledge & guides' }).click()
  await expect(page.getByRole('heading', { name: 'How support requests are handled' })).toBeVisible()
  await scan()
})
