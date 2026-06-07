import { UserManager, WebStorageStateStore } from 'oidc-client-ts'

const appOrigin = window.location.origin

export const userManager = new UserManager({
  authority: import.meta.env.VITE_KEYCLOAK_AUTHORITY,
  client_id: import.meta.env.VITE_KEYCLOAK_CLIENT_ID,
  redirect_uri: `${appOrigin}/`,
  post_logout_redirect_uri: `${appOrigin}/`,
  silent_redirect_uri: `${appOrigin}/`,
  response_type: 'code',
  scope: 'openid profile email',
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
})
