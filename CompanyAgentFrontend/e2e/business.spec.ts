import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

async function mockBusiness(
  page: Page,
  options: { failFirst?: boolean; expired?: boolean; externalApprovalOnPoll?: boolean } = {},
) {
  // Intercept browser API requests rather than contacting production or a model.
  // These mutable fixture values emulate account status, transient failure, expiry,
  // and a supervisor decision observed on a later poll. IDs are captured for replay checks.
  let status = 'pending'
  let calls = 0
  let polls = 0
  const requestIds: string[] = []
  const action = () => ({
    id: 'action-demo',
    tool: 'refund',
    status,
    reason_code: status === 'pending' ? 'HIGH_VALUE_REVIEW' : 'SUPERVISOR_APPROVED',
    amount_cents: 45000,
    summary: 'Refund 450.00 EUR for ch_1',
  })
  await page.route(/\/api\/python\//, async (route) => {
    // Return the object shape each real endpoint promises. Unrelated admin endpoints
    // are also stubbed so the existing staff workspace can load without external services.
    const path = new URL(route.request().url()).pathname
    let body: unknown = {}
    if (path.endsWith('/health'))
      body = { status: 'ok', capabilities: ['business_workflows', 'approvals', 'demo_self_approval'] }
    else if (path.endsWith('/customer/personas'))
      body = {
        items: [
          {
            id: 'p_b2b',
            name: 'Farid Haddad',
            tagline: 'A payment requiring review.',
            starter: 'Please refund my payment.',
          },
        ],
      }
    else if (path.endsWith('/customer/sessions'))
      body = {
        token: 'customer-test',
        conv_id: 'conv-test',
        expires_in: 7200,
        customer: { name: 'Farid Haddad' },
      }
    else if (path.endsWith('/customer/chat')) {
      const request = route.request().postDataJSON()
      requestIds.push(request.request_id)
      calls++
      if (options.expired) {
        await route.fulfill({ status: 401, json: { detail: 'Demo session expired' } })
        return
      }
      if (options.failFirst && calls === 1) {
        // Failure says the outcome is uncertain. The UI must retry the same request ID,
        // not infer rollback or duplicate the visible customer message.
        await route.fulfill({ status: 503, json: { detail: 'Connection interrupted after submission' } })
        return
      }
      expect(route.request().headers().authorization).toBe('Bearer customer-test')
      body = {
        response: 'Your request is submitted for review. It has not been refunded yet.',
        actions: [action()],
      }
    } else if (path.endsWith('/decide')) {
      expect(route.request().headers().authorization).toBe(
        path.includes('/customer/demo/') ? 'Bearer customer-test' : 'Bearer admin-test',
      )
      status = route.request().postDataJSON().decision === 'approve' ? 'executed' : 'rejected'
      body = { action: action() }
    } else if (path.endsWith('/actions')) {
      polls++
      if (options.externalApprovalOnPoll && polls > 1) status = 'executed'
      body = { items: [action()] }
    } else if (path.endsWith('/admin/login')) body = { access_token: 'admin-test', username: 'operator' }
    else if (path.endsWith('/admin/overview')) body = { knowledge_chunks: 12, alerts: [] }
    else if (path.endsWith('/knowledge/stats')) body = { total_chunks: 12 }
    else if (path.endsWith('/monitor')) body = { status: 'ok', agents: {} }
    await route.fulfill({ json: body })
  })
  return requestIds
}

async function openDemo(page: Page) {
  // Follow accessible button/textbox labels just like a user, then submit one message.
  // This helper intentionally uses the UI instead of directly setting component state.
  await page.goto('/')
  await page.getByRole('button', { name: 'Try demo accounts' }).click()
  await page.getByRole('button', { name: "Use Farid Haddad's account" }).click()
  await expect(page.getByRole('textbox', { name: 'Message your demo account support' })).toHaveValue(
    'Please refund my payment.',
  )
  await page.getByRole('button', { name: 'Send to account support' }).click()
}

test('demo refund approval, reload, mobile layout and accessibility', async ({ page }) => {
  // A narrow viewport checks wrapping; axe checks its supported accessibility rules.
  // The final reload verifies tab storage restoration, not real backend persistence.
  await mockBusiness(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await openDemo(page)
  await expect(page.getByText('Pending review', { exact: true })).toBeVisible()
  await page
    .getByLabel('Decision note (required for rejection or high-value approval)')
    .fill('Verified the demo refund')
  await page.getByRole('button', { name: 'Approve as supervisor' }).click()
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(
    result.violations.map((row) => ({ id: row.id, targets: row.nodes.map((node) => node.target) })),
  ).toEqual([])
  await page.screenshot({ path: 'test-results/visual/business-mobile.png', fullPage: true })
  await page.reload()
  await page.getByRole('button', { name: 'Try demo accounts' }).click()
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
})

test('interrupted message retry keeps the same request ID', async ({ page }) => {
  // Network uncertainty must preserve identity and show one customer row after retry.
  const ids = await mockBusiness(page, { failFirst: true })
  await openDemo(page)
  await page.getByRole('button', { name: 'Retry this message' }).click()
  await expect(page.getByText('Pending review', { exact: true })).toBeVisible()
  expect(ids).toHaveLength(2)
  expect(ids[0]).toBe(ids[1])
  await expect(page.getByText('Please refund my payment.', { exact: true })).toHaveCount(1)
})

test('expired demo sessions are read only', async ({ page }) => {
  // Keep historical facts visible but disable new messages after a server 401.
  await mockBusiness(page, { expired: true })
  await openDemo(page)
  await expect(
    page.getByText('This demo session has expired. Start a new demo account to continue.'),
  ).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Message your demo account support' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'New demo account' })).toBeEnabled()
})

test('pending operations poll and observe an external supervisor decision', async ({ page }) => {
  // Browser-clock control advances the eight-second polling timer without real waiting.
  // A later mocked status, not an optimistic click, makes the card display Completed.
  await mockBusiness(page, { externalApprovalOnPoll: true })
  await page.clock.install()
  const firstPoll = page.waitForResponse((response) => response.url().includes('/customer/actions?'))
  await openDemo(page)
  await firstPoll
  await expect(page.getByText('Pending review', { exact: true })).toBeVisible()
  await page.clock.fastForward(8000)
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
})

test('staff approvals use administrator credentials', async ({ page }) => {
  // Exercise the staff login/navigation path and assert its requests carry the staff
  // fixture token, not the customer demo token used by self-approval controls.
  await mockBusiness(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'Staff sign in' }).click()
  await page.getByLabel('Username', { exact: true }).fill('operator')
  await page.getByLabel('Password', { exact: true }).fill('fixture')
  await page.getByRole('button', { name: 'Enter workspace' }).click()
  await page.getByRole('button', { name: 'Account approvals' }).click()
  await expect(page.getByText('Pending review', { exact: true })).toBeVisible()
  await page.getByLabel('Decision note (required for rejection or high-value approval)').fill('Verified')
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/visual/business-admin.png', fullPage: true })
})
