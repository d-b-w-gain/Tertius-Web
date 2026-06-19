import { useAuth } from './AuthProvider'

function getDisplayName(user: ReturnType<typeof useAuth>['user']) {
  if (!user) {
    return 'Guest'
  }

  return user.email || 'Account'
}

export function LoginStateWidget() {
  const { authMode, user, login, logout } = useAuth()
  const isGuest = authMode === 'guest'

  return (
    <div className="mb-2 flex items-center gap-2 rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1 text-sm">
      <span className="max-w-40 truncate text-slate-300" title={getDisplayName(user)}>
        {getDisplayName(user)}
      </span>
      <button
        type="button"
        onClick={() => {
          void (isGuest ? login() : logout())
        }}
        className="rounded px-2 py-1 text-cyan-300 transition-colors hover:bg-slate-800 hover:text-cyan-200"
      >
        {isGuest ? 'Log in' : 'Log out'}
      </button>
    </div>
  )
}
