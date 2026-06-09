export async function apiFetch(
  url: string,
  getAccessToken: () => Promise<string>,
  init: RequestInit = {},
) {
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${await getAccessToken()}`)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(url, { ...init, headers })
  return response
}
