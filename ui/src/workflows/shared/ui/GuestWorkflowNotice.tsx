type GuestWorkflowNoticeProps = {
  title: string
  message: string
  onLogin: () => void
}

export function GuestWorkflowNotice({ title, message, onLogin }: GuestWorkflowNoticeProps) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center bg-slate-900 p-6 text-slate-300">
      <div className="max-w-md rounded border border-slate-800 bg-slate-950/70 p-5 shadow-lg">
        <h2 className="text-base font-semibold text-slate-100">{title}</h2>
        <p className="mt-2 text-sm text-slate-400">{message}</p>
        <button
          type="button"
          onClick={() => {
            void onLogin()
          }}
          className="mt-4 rounded bg-cyan-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-cyan-500"
        >
          Log in
        </button>
      </div>
    </div>
  )
}
