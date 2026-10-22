import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, type APIRequestContext } from '@playwright/test'

// The project prefix identifies resources owned by this synthetic acceptance run.
// Never inspect a developer's environment/configuration or operate on the old stack.
const project = process.env.CLOSEOUT_COMPOSE_PROJECT || 'companyagent-closeout-20261022'
const composeFile = fileURLToPath(
  new URL('../../CompanyAgent/acceptance/docker-compose.yml', import.meta.url),
)
if (!/^companyagent-closeout-[a-z0-9-]+$/.test(project)) throw new Error('Invalid acceptance project')

function docker(...args: string[]) {
  // Pass each argument directly, with no shell interpolation. Only selected labels,
  // scalar database counts and this project's synthetic logs can enter test output.
  return execFileSync('docker', args, { encoding: 'utf8', timeout: 120_000, windowsHide: true }).trim()
}

function compose(...args: string[]) {
  return docker('compose', '-p', project, '-f', composeFile, ...args)
}

async function ready(request: APIRequestContext) {
  // Startup and graceful recreation take time. Poll the actual proxied health route
  // with a finite deadline rather than sleeping or declaring readiness from a PID.
  await expect
    .poll(
      async () => {
        try {
          return (await request.get('/api/python/health')).status()
        } catch {
          return 0
        }
      },
      { timeout: 90_000, intervals: [500, 1000, 2000] },
    )
    .toBe(200)
}

function snapshot() {
  // Read only aggregate counts from this owned container's synthetic business DB.
  // No tokens, customer records, Redis data or developer files are printed.
  const program =
    'import json,sqlite3; db=sqlite3.connect("/app/data/business/business.db"); ' +
    'print(json.dumps({name:db.execute("SELECT COUNT(*) FROM "+name).fetchone()[0] ' +
    'for name in ("refunds","actions","customer_requests")}))'
  return JSON.parse(compose('exec', '-T', 'companyagent', 'python', '-c', program))
}

test.beforeAll(async ({ request }) => {
  // Refuse an unrelated service on port 18874: verify both project/service labels
  // and the actual published loopback binding before making any test HTTP request.
  const ids = docker(
    'ps',
    '--filter',
    `label=com.docker.compose.project=${project}`,
    '--filter',
    'label=com.docker.compose.service=frontend',
    '--format',
    '{{.ID}}',
  ).split('\n')
  expect(ids).toHaveLength(1)
  expect(ids[0]).not.toBe('')
  expect(docker('port', ids[0], '80/tcp')).toBe('127.0.0.1:18874')
  await ready(request)
})

test('real staff sign-in approves a customer request through built Nginx and api.main', async ({
  page,
  browser,
}) => {
  // The customer and staff use separate browser storage, real HTTP and the existing
  // login endpoint. No page.route or custom authentication callback is installed.
  await page.goto('/')
  await page.getByRole('button', { name: 'Try demo accounts' }).click()
  await page.getByRole('button', { name: "Use Farid Haddad's account" }).click()
  await page.getByRole('button', { name: 'Send to account support' }).click()
  await expect(page.getByText('Pending review', { exact: true })).toBeVisible()
  // Customer self-approval is disabled in the stack; a hidden button is also backed
  // by real backend enforcement, covered in the lifecycle API case below.
  await expect(page.getByRole('button', { name: 'Approve as supervisor' })).toHaveCount(0)
  const staff = await browser.newContext()
  try {
    const admin = await staff.newPage()
    await admin.goto('http://127.0.0.1:18874')
    await admin.getByRole('button', { name: 'Staff sign in' }).click()
    await admin.getByLabel('Username', { exact: true }).fill('admin')
    await admin.getByLabel('Password', { exact: true }).fill('acceptance-staff-not-a-secret')
    await admin.getByRole('button', { name: 'Enter workspace' }).click()
    await expect(admin.getByText('Healthy', { exact: true })).toBeVisible()
    await admin.getByRole('button', { name: 'Account approvals', exact: true }).click()
    // One worker owns this stack. The newest matching action is the one just
    // created above; earlier accepted runs may have retained completed test rows.
    const card = admin.locator('.business-action').filter({ hasText: 'Refund 450.00 EUR' }).first()
    await expect(card.getByText('Pending review', { exact: true })).toBeVisible()
    await card
      .getByLabel('Decision note (required for rejection or high-value approval)')
      .fill('Verified synthetic account')
    await card.getByRole('button', { name: 'Approve', exact: true }).click()
    await expect(card.getByText('Completed', { exact: true })).toBeVisible()
    await page.reload()
    // Reload restores the account data but intentionally returns to general support;
    // reopen the existing demo rather than claiming the app auto-opens this panel.
    await page.getByRole('button', { name: 'Try demo accounts' }).click()
    await expect(page.getByRole('heading', { name: 'Support for Farid Haddad' })).toBeVisible()
    await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  } finally {
    await staff.close()
  }
})

test('non-root volume survives recreation and feature rollback preserves general support', async ({
  request,
}) => {
  // Create a separate account and call the main /chat business branch itself.
  const created = await request.post('/api/python/customer/sessions', { data: { persona_id: 'p_duplicate' } })
  expect(created.status()).toBe(200)
  const session = await created.json()
  const headers = { Authorization: `Bearer ${session.token}` }
  const data = {
    message: 'I was charged twice. Please refund the duplicate payment.',
    conv_id: session.conv_id,
    request_id: 'acceptance-refund',
    user_id: 'forged-other-customer',
  }
  expect(
    (await request.post('/api/python/chat', { headers: { Authorization: 'Bearer invalid' }, data })).status(),
  ).toBe(401)
  expect((await request.get('/api/python/admin/actions', { headers })).status()).toBe(401)
  const sent = await request.post('/api/python/chat', { headers, data })
  expect(sent.status()).toBe(200)
  const reply = await sent.json()
  expect(reply.actions[0].status).toBe('executed')
  expect(
    (
      await request.post(`/api/python/customer/demo/actions/${reply.operation_id}/decide`, {
        headers,
        data: { decision: 'approve', note: 'Not staff' },
      })
    ).status(),
  ).toBe(404)
  expect(compose('exec', '-T', 'companyagent', 'id', '-u')).toBe('1000')
  const before = snapshot()
  const previousContainer = compose('ps', '-q', 'companyagent')
  // Recreate (not merely restart) so the container writable layer is replaced.
  // Nginx reload resolves the new container address using its unchanged config.
  compose('up', '-d', '--force-recreate', '--no-deps', 'companyagent')
  compose('exec', '-T', 'frontend', 'nginx', '-s', 'reload')
  await ready(request)
  expect(compose('ps', '-q', 'companyagent')).not.toBe(previousContainer)
  const replay = await request.post('/api/python/chat', { headers, data })
  expect(replay.status()).toBe(200)
  expect(await replay.json()).toEqual(reply)
  expect(snapshot()).toEqual(before)

  // Disabling the capability must hide its routes without deleting its volume.
  // A uniquely owned temp FILE supplies only this override, never a real .env.
  const folder = mkdtempSync(join(tmpdir(), 'companyagent-acceptance-'))
  const override = join(folder, 'disabled.yml')
  writeFileSync(
    override,
    '# Disable only the optional business feature for this test.\nservices:\n  companyagent:\n    environment:\n      BUSINESS_WORKFLOWS_ENABLED: "false"\n',
  )
  try {
    docker(
      'compose',
      '-p',
      project,
      '-f',
      composeFile,
      '-f',
      override,
      'up',
      '-d',
      '--force-recreate',
      '--no-deps',
      'companyagent',
    )
    compose('exec', '-T', 'frontend', 'nginx', '-s', 'reload')
    await ready(request)
    expect((await (await request.get('/api/python/health')).json()).capabilities).toEqual([])
    expect((await request.post('/api/python/chat', { headers, data })).status()).toBe(404)
    const general = await request.post('/api/python/chat', {
      data: { message: 'Hello, how can you help me?', user_id: 'acceptance-general' },
    })
    expect(general.status()).toBe(200)
    expect((await general.json()).response).toBeTruthy()
  } finally {
    // Restore the original capability even after an assertion fails. Unlink only
    // the exact file created above, then remove the now-empty owned directory.
    compose('up', '-d', '--force-recreate', '--no-deps', 'companyagent')
    compose('exec', '-T', 'frontend', 'nginx', '-s', 'reload')
    unlinkSync(override)
    rmdirSync(folder)
    await ready(request)
  }
  expect(snapshot()).toEqual(before)
  const restored = await request.get(`/api/python/customer/actions?conv_id=${session.conv_id}`, { headers })
  expect(restored.status()).toBe(200)
  expect((await restored.json()).items[0].id).toBe(reply.operation_id)
})
