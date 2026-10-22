/**
 * Start exactly one loopback fixture service for Playwright, never production.
 * `api` runs the Python-only business host; `web` serves the real React source.
 * Ports are fixed and server reuse is disabled in the Playwright configuration.
 * No .env file is loaded: Vite receives a newly created, empty environment folder.
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const backend = resolve(frontend, '../CompanyAgent')
const mode = process.argv[2]

if (mode === 'api') {
  // The feature worktree may share the original repository's Python 3.12 venv.
  // An explicit interpreter path is passed as one spawn argument, never a shell
  // command, so spaces/Chinese characters cannot split or execute part of a path.
  const localPython = resolve(
    frontend,
    process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python',
  )
  const python = process.env.BUSINESS_E2E_PYTHON || (existsSync(localPython) ? localPython : 'python')
  // Forward only OS execution/temp settings. No credential, proxy, Python startup
  // hook or model endpoint from the developer's environment enters this fixture.
  const env = { PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' }
  // Hosted Unix Python installations may need these OS library search paths;
  // forwarding loader paths does not enable dotenv or application configuration.
  for (const key of [
    'PATH',
    'Path',
    'SystemRoot',
    'WINDIR',
    'TEMP',
    'TMP',
    'HOME',
    'USERPROFILE',
    'LD_LIBRARY_PATH',
    'DYLD_LIBRARY_PATH',
  ]) {
    if (process.env[key]) env[key] = process.env[key]
  }
  const child = spawn(python, ['tests/browser_business_server.py'], {
    cwd: backend,
    env,
    stdio: 'inherit',
    windowsHide: true,
  })
  child.on('error', (error) => {
    console.error(`Cannot start the Python 3.12 fixture: ${error.message}`)
    process.exitCode = 1
  })
  child.on('exit', (code) => {
    process.exitCode = code ?? 1
  })
  // Normal termination lets Uvicorn close requests and remove its owned temp DB.
  // Force-killing a process tree cannot promise lifespan cleanup on every OS.
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => child.kill(signal))
} else if (mode === 'web') {
  const { createServer } = await import('vite')
  const { default: react } = await import('@vitejs/plugin-react')
  const envFolder = mkdtempSync(join(tmpdir(), 'companyagent-vite-env-'))
  const cleanup = () => {
    // Delete only the exact newly created folder, after verifying its resolved
    // parent/name. Never clean a computed project directory or another user's data.
    if (
      dirname(envFolder) === resolve(tmpdir()) &&
      basename(envFolder).startsWith('companyagent-vite-env-')
    ) {
      rmSync(envFolder, { recursive: true, force: true })
    }
  }
  process.once('exit', cleanup)
  const server = await createServer({
    // The React source and proxy path are real; this separate configuration keeps
    // test ports/env ownership apart from the developer's normal Vite server.
    configFile: false,
    root: frontend,
    plugins: [react()],
    envDir: envFolder,
    envPrefix: 'BROWSER_FIXTURE_PUBLIC_',
    define: {
      'import.meta.env.VITE_PYTHON_API_URL': JSON.stringify('/api/python'),
      'import.meta.env.VITE_JAVA_API_URL': JSON.stringify('/api/java'),
      'import.meta.env.VITE_DOTNET_API_URL': JSON.stringify('/api/dotnet'),
    },
    server: {
      host: '127.0.0.1',
      port: 5174,
      strictPort: true,
      proxy: {
        '/api/python': {
          target: 'http://127.0.0.1:8873',
          changeOrigin: true,
          headers: { 'X-Forwarded-Prefix': '/api/python' },
          rewrite: (path) => path.replace(/^\/api\/python/, ''),
        },
      },
    },
  })
  await server.listen()
  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.once(signal, async () => {
      await server.close()
      cleanup()
      process.exit(0)
    })
  }
} else {
  throw new Error('Expected api or web; this launcher has no production mode')
}
