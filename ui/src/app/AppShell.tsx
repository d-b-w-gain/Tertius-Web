import type { ReactNode } from 'react'

type AppShellProps = {
  buildInfoTooltip: string
  children: ReactNode
  footer: ReactNode
  isSidebarOpen: boolean
  navigation: ReactNode
  notification?: ReactNode
  onCloseSidebar: () => void
  sidebar: ReactNode
}

export function AppShell({ buildInfoTooltip, children, footer, isSidebarOpen, navigation, notification, onCloseSidebar, sidebar }: AppShellProps) {
  return (
    <div className="flex h-screen w-screen bg-slate-950 text-slate-100 overflow-hidden font-sans">
      {isSidebarOpen && <div className="fixed inset-0 bg-black/50 z-10 md:hidden transition-opacity" onClick={onCloseSidebar} />}
      <div className={`absolute z-20 h-full md:relative md:h-auto border-r border-slate-800 bg-slate-900/95 md:bg-slate-900/50 flex flex-col transition-all duration-300 ease-in-out overflow-hidden ${isSidebarOpen ? 'w-96 translate-x-0' : 'w-96 -translate-x-full md:w-0 md:translate-x-0 md:border-r-0'}`}>
        <div className="p-4 border-b border-slate-800 flex items-center justify-between min-w-[20rem]">
          <div>
            <h1 className="text-xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent" title={buildInfoTooltip}>Tertius</h1>
            <p className="text-xs text-slate-500 mt-1">Open Source CAD Toolkit</p>
          </div>
          <button className="md:hidden text-slate-400 hover:text-white" onClick={onCloseSidebar}>
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="flex-1 flex flex-col min-h-0 min-w-[20rem]">{sidebar}</div>
      </div>
      <div className="flex-1 flex flex-col min-w-0 relative">
        {notification}
        {navigation}
        {children}
      </div>
      {footer}
    </div>
  )
}
