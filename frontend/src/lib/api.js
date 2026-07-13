const PASSWORD_KEY = 'codebuddy2api.servicePassword'

export function getStoredPassword() {
  return localStorage.getItem(PASSWORD_KEY) || ''
}

export function setStoredPassword(password) {
  if (password) localStorage.setItem(PASSWORD_KEY, password)
  else localStorage.removeItem(PASSWORD_KEY)
}

export function authHeaders(extra = {}) {
  const password = getStoredPassword()
  return {
    'Content-Type': 'application/json',
    ...(password ? { Authorization: `Bearer ${password}` } : {}),
    ...extra,
  }
}

export function isOAuthPendingError(error) {
  if (!error) return false
  if (error.code === 11217) return true
  const markers = ['authorization_pending', 'slow_down']
  return markers.includes(error.error) || markers.includes(error.message)
}

export async function apiFetch(path, options = {}, authenticated = true) {
  const headers = authenticated
    ? authHeaders(options.headers || {})
    : { 'Content-Type': 'application/json', ...(options.headers || {}) }
  const response = await fetch(path, { ...options, headers })
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    let body = null
    try {
      body = await response.json()
      message = body.detail || body.error_description || body.message || body.error || message
    } catch {
      // Keep the HTTP fallback.
    }
    const error = new Error(message)
    error.status = response.status
    if (body && typeof body === 'object') {
      error.error = body.error
      error.code = body.code
      error.body = body
    }
    throw error
  }
  const contentType = response.headers.get('content-type') || ''
  return contentType.includes('application/json') ? response.json() : response
}

export async function copyText(value) {
  await navigator.clipboard.writeText(String(value))
}
