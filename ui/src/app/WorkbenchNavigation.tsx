import { LoginStateWidget } from '../auth/LoginStateWidget'
import { AboutMenu } from './AboutMenu'

export type WorkbenchTab = 'generate' | 'extus' | 'intus' | 'timus' | 'octavus' | 'site' | 'structural'

type WorkbenchNavigationProps = {
  activeTab: WorkbenchTab
  canUseSiteWorkbench: boolean
  canUseStructuralWorkbench: boolean
  onSelectTab: (tab: WorkbenchTab) => void
  onToggleSidebar: () => void
}

const tabClassName = (isActive: boolean, activeTextClass: string) =>
  `px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${isActive ? `bg-slate-950 ${activeTextClass} font-medium border-slate-800` : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`

export function WorkbenchNavigation({ activeTab, canUseSiteWorkbench, canUseStructuralWorkbench, onSelectTab, onToggleSidebar }: WorkbenchNavigationProps) {
  return (
    <div className="flex bg-slate-900 border-b border-slate-800 px-4 pt-4 gap-2 overflow-x-auto whitespace-nowrap scrollbar-hide">
      <button onClick={onToggleSidebar} className="p-2 mb-2 mr-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors shrink-0 flex items-center justify-center" title="Toggle Sidebar">
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
      </button>
      <button onClick={() => onSelectTab('generate')} className={tabClassName(activeTab === 'generate', 'text-cyan-300')}>Generate Design</button>
      <button onClick={() => onSelectTab('extus')} className={tabClassName(activeTab === 'extus', 'text-cyan-300')}>👁️ Extus Viewport</button>
      <button onClick={() => onSelectTab('intus')} className={tabClassName(activeTab === 'intus', 'text-indigo-300')}>⚙️ Intus Compiler</button>
      <button onClick={() => onSelectTab('timus')} className={tabClassName(activeTab === 'timus', 'text-emerald-300')}>📐 Timus Drafting</button>
      <button onClick={() => onSelectTab('octavus')} className={tabClassName(activeTab === 'octavus', 'text-amber-300')}>🛒 Procurement</button>
      {canUseSiteWorkbench && <button onClick={() => onSelectTab('site')} className={tabClassName(activeTab === 'site', 'text-cyan-300')}>🧭 Site</button>}
      {canUseStructuralWorkbench && <button onClick={() => onSelectTab('structural')} className={tabClassName(activeTab === 'structural', 'text-orange-300')}>🏗️ Structural</button>}
      <div className="ml-auto flex items-center space-x-2 mr-4">
        <AboutMenu />
        <LoginStateWidget />
      </div>
    </div>
  )
}
