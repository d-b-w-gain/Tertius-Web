import { UserManager, WebStorageStateStore } from 'oidc-client-ts'

const appOrigin = window.location.origin
const authEnv = import.meta.env.VITE_KEYCLOAK_AUTHORITY || '/realms/tertius'
const clientId = import.meta.env.VITE_KEYCLOAK_CLIENT_ID || 'tertius-ui'
const authorityUrl = authEnv.startsWith('http') ? authEnv : `${appOrigin}${authEnv}`

export const userManager = new UserManager({
  authority: authorityUrl,
  client_id: clientId,
  redirect_uri: `${appOrigin}/`,
  post_logout_redirect_uri: `${appOrigin}/`,
  silent_redirect_uri: `${appOrigin}/`,
  response_type: 'code',
  scope: 'openid profile email',
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
})
