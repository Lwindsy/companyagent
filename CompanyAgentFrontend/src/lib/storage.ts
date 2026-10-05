// Storage may be unavailable in privacy mode or full. The UI remains usable in memory.
export function readStorage(storage: 'localStorage' | 'sessionStorage', key: string): unknown {
  try {
    return JSON.parse(window[storage].getItem(key) || 'null') as unknown
  } catch {
    return null
  }
}

export function writeStorage(
  storage: 'localStorage' | 'sessionStorage',
  key: string,
  value: unknown,
): boolean {
  try {
    window[storage].setItem(key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}

export function removeStorage(storage: 'localStorage' | 'sessionStorage', key: string) {
  try {
    window[storage].removeItem(key)
  } catch {
    /* Nonessential cleanup. */
  }
}

export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

export function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}
