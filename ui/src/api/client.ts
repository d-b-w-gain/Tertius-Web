import { userManager } from '../auth/keycloak'

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
  if (response.status !== 401) {
    return response
  }

  try {
    const renewed = await userManager.signinSilent()
    if (!renewed) {
      throw new Error('Silent sign-in did not return a user')
    }
    headers.set('Authorization', `Bearer ${renewed.access_token}`)
    const retryResponse = await fetch(url, { ...init, headers })
    if (retryResponse.status === 401) {
      await userManager.signinRedirect()
    }
    return retryResponse
  } catch {
    await userManager.signinRedirect()
    return response
  }
}
