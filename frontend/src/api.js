const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    credentials: 'include',
    ...options,
  })
  if (!response.ok) throw new Error(`API ${response.status}`)
  if (response.status === 204) return null
  return response.json()
}

export const api = {
  dashboard: () => request('/dashboard'),
  channels: () => request('/channels'),
  recordings: () => request('/recordings'),
  createChannel: (platform, username) => request('/channels', {
    method: 'POST', body: JSON.stringify({ platform, username }),
  }),
}
