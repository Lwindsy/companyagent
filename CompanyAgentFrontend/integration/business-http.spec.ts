import { expect, test, type Page } from '@playwright/test'

// These credentials are freshly issued by the real session service for synthetic
// accounts. They stay in test memory; no real key, database or browser profile is read.
interface FixtureSession {
  token: string
  conv_id: string
}

async function openAccount(page: Page, name: string): Promise<FixtureSession> {
  // Observe real responses instead of intercepting/fulfilling browser requests.
  // The readiness marker prevents a successful run against an unrelated API host.
  const health = await page.request.get('/api/python/health')
  expect((await health.json()).fixture).toBe('offline_business_http_fixture')
  await page.goto('/')
  await page.getByRole('button', { name: 'Try demo accounts' }).click()
  const created = page.waitForResponse((response) => response.url().endsWith('/customer/sessions'))
  await page.getByRole('button', { name: `Use ${name}'s account` }).click()
  const response = await created
  expect(response.status()).toBe(200)
  const session = (await response.json()) as FixtureSession
  await expect(page.getByRole('heading', { name: `Support for ${name}` })).toBeVisible()
  return session
}

async function send(page: Page, message?: string) {
  // Fill only when overriding the persona starter. Await the real route's JSON
  // response and restored button state before submitting another independent turn.
  if (message !== undefined)
    await page.getByRole('textbox', { name: 'Message your demo account support' }).fill(message)
  const completed = page.waitForResponse((response) => response.url().endsWith('/customer/chat'))
  await page.getByRole('button', { name: 'Send to account support' }).click()
  const response = await completed
  expect(response.status()).toBe(200)
  await expect(page.getByRole('textbox', { name: 'Message your demo account support' })).toBeEnabled()
  return response.json()
}

async function snapshot(page: Page, session: FixtureSession) {
  // This read-only fixture endpoint counts actual committed SQLite rows in the
  // authenticated scope. It cannot manufacture a successful refund for the UI.
  const response = await page.request.get('/api/python/__test__/snapshot', {
    headers: { Authorization: `Bearer ${session.token}` },
  })
  expect(response.status()).toBe(200)
  return response.json()
}

test('Anna commits one duplicate refund and reload observes real persisted state', async ({ page }) => {
  // The duplicate pair's later charge must be the only refunded one, including
  // after the browser reconstructs its tab state and fetches real action status.
  const session = await openAccount(page, 'Anna Berg')
  const reply = await send(page)
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  const before = await snapshot(page, session)
  expect(before.refunds).toBe(1)
  expect(before.requests).toBe(1)
  expect(before.overview.charges.map((charge: { refunded: number }) => charge.refunded)).toEqual([0, 1])
  await page.reload()
  await page.getByRole('button', { name: 'Try demo accounts' }).click()
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  const actions = await page.request.get(`/api/python/customer/actions?conv_id=${session.conv_id}`, {
    headers: { Authorization: `Bearer ${session.token}` },
  })
  expect((await actions.json()).items[0].id).toBe(reply.operation_id)
  expect(await snapshot(page, session)).toEqual(before)
})

test('Farid approval changes the real ledger and another account cannot approve it', async ({ page }) => {
  // A pending card must correspond to zero refunds. Cross-scope decisions are
  // denied by the real router/domain before the owner's demo approval is sent.
  const session = await openAccount(page, 'Farid Haddad')
  const reply = await send(page)
  await expect(page.getByText('Pending review', { exact: true })).toBeVisible()
  expect((await snapshot(page, session)).refunds).toBe(0)
  const otherResponse = await page.request.post('/api/python/customer/sessions', {
    data: { persona_id: 'p_duplicate' },
  })
  expect(otherResponse.status()).toBe(200)
  const other = (await otherResponse.json()) as FixtureSession
  const forbidden = await page.request.post(
    `/api/python/customer/demo/actions/${reply.operation_id}/decide`,
    {
      headers: { Authorization: `Bearer ${other.token}` },
      data: { decision: 'approve', note: 'Must not authorize another sandbox' },
    },
  )
  expect(forbidden.status()).toBe(404)
  expect((await snapshot(page, other)).refunds).toBe(0)
  expect((await snapshot(page, session)).refunds).toBe(0)
  const approved = page.waitForResponse((response) => response.url().endsWith('/decide'))
  await page
    .getByLabel('Decision note (required for rejection or high-value approval)')
    .fill('Verified synthetic account')
  await page.getByRole('button', { name: 'Approve as supervisor' }).click()
  expect((await approved).status()).toBe(200)
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  expect((await snapshot(page, session)).refunds).toBe(1)
  const status = await send(page, 'Check my request status.')
  expect(status.response).toContain('executed')
})

test('Chloe denial, renewal toggles and repeated handoff persist the intended facts', async ({ page }) => {
  // Denial must leave zero refunds. Cancel/restore/cancel are three legitimate
  // new operations, whereas repeated human requests reuse one open ticket.
  const session = await openAccount(page, 'Chloe Martin')
  await send(page)
  await expect(page.getByText('Not allowed', { exact: true })).toBeVisible()
  expect((await snapshot(page, session)).refunds).toBe(0)
  for (const message of ['Cancel renewal.', 'Restore renewal.', 'Cancel renewal.']) await send(page, message)
  const first = await send(page, 'Please connect me to a human.')
  const second = await send(page, 'Please connect me to a human.')
  expect(second.handoff.id).toBe(first.handoff.id)
  await expect(page.getByText(/Human review recorded · Ticket/)).toBeVisible()
  const saved = await snapshot(page, session)
  expect(saved.refunds).toBe(0)
  expect(saved.actions).toBe(4)
  expect(saved.tickets).toBe(1)
  expect(saved.overview.cancel_at_period_end).toBe(true)
})

test('a lost reply after commit retries the same request without another extraction or refund', async ({
  page,
  context,
}) => {
  // The server drops only the HTTP reply, AFTER writing the refund and saved
  // response. Unlike the old mocked-503 test, the ledger already contains a refund.
  const session = await openAccount(page, 'Anna Berg')
  const ids: string[] = []
  page.on('request', (request) => {
    if (request.url().endsWith('/customer/chat')) ids.push(request.postDataJSON().request_id)
  })
  await context.setExtraHTTPHeaders({ 'X-Browser-Test-Lose-Reply': 'once' })
  const lost = page.waitForResponse((response) => response.url().endsWith('/customer/chat'))
  await page.getByRole('button', { name: 'Send to account support' }).click()
  expect((await lost).status()).toBe(503)
  await expect(page.getByRole('button', { name: 'Retry this message' })).toBeVisible()
  const committed = await snapshot(page, session)
  expect(committed.refunds).toBe(1)
  expect(committed.requests).toBe(1)
  const recovered = page.waitForResponse((response) => response.url().endsWith('/customer/chat'))
  await page.getByRole('button', { name: 'Retry this message' }).click()
  expect((await recovered).status()).toBe(200)
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  expect(ids).toHaveLength(2)
  expect(ids[1]).toBe(ids[0])
  expect(await snapshot(page, session)).toEqual(committed)
  await expect(
    page.getByText('I was charged twice. Please refund the duplicate payment.', { exact: true }),
  ).toHaveCount(1)
  await page.screenshot({ path: 'test-results/integration/business-http-recovered.png', fullPage: true })
})
