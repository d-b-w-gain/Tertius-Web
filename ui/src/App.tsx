import { useEffect, useRef, useState } from 'react'
import { IntusWindow } from './workflows/intus/IntusWindow'
import { ExtusWindow } from './workflows/extus/ExtusWindow'
import { ArtusWindow } from './workflows/artus/ArtusWindow'
import { TimusWindow } from './workflows/timus/TimusWindow'
import { OctavusWindow } from './workflows/octavus/OctavusWindow'
import { GenerateDesignWindow } from './workflows/generate/GenerateDesignWindow'
import { AiBudgetGauge } from './workflows/generate/AiBudgetGauge'
import { useAuth } from './auth/AuthProvider'
import { LoginStateWidget } from './auth/LoginStateWidget'
import { GUEST_WORKSPACE_KEY } from './workflows/shared/guestWorkspace'
import { importGuestWorkspace } from './workflows/shared/guestImport'
import { resolveWorkflowServerUrl } from './workflows/shared/apiConfig'

function App() {
  const { authMode, getAccessToken, isLoading } = useAuth()
  const [activeTab, setActiveTab] = useState('generate')
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [showImportBanner, setShowImportBanner] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [isImporting, setIsImporting] = useState(false)
  const previousAuthMode = useRef(authMode)
  const buildInfoTooltip = `Commit ${__GIT_COMMIT__}\nDate ${__GIT_COMMIT_DATE__}`
  const intusServerUrl = resolveWorkflowServerUrl('intus', import.meta.env?.VITE_API_URL)

  useEffect(() => {
    if (authMode === 'guest') {
      sessionStorage.setItem('tertius_guest_seen', 'true')
    }

    const transitionedToAuth = previousAuthMode.current === 'guest' && authMode === 'authenticated'
    const sawGuestThisSession = sessionStorage.getItem('tertius_guest_seen') === 'true'
    if ((transitionedToAuth || sawGuestThisSession) && authMode === 'authenticated' && localStorage.getItem(GUEST_WORKSPACE_KEY)) {
      setShowImportBanner(true)
    }
    previousAuthMode.current = authMode
  }, [authMode])

  const handleImportGuestWorkspace = async () => {
    setIsImporting(true)
    setImportError(null)
    try {
      const result = await importGuestWorkspace({ getAccessToken })
      window.dispatchEvent(new CustomEvent('tertius:guest-imported', { detail: result }))
      setShowImportBanner(false)
    } catch (error) {
      setImportError(error instanceof Error ? error.message : 'Failed to import guest workspace')
    } finally {
      setIsImporting(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-slate-950 text-slate-300">
        Loading...
      </div>
    )
  }

  return (
    <div className="flex h-screen w-screen bg-slate-950 text-slate-100 overflow-hidden font-sans">
      {/* Mobile Backdrop */}
      {isSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-10 md:hidden transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      {/* Permanent Artus Feature Tree (Sidebar) */}
      <div 
        className={`absolute z-20 h-full md:relative md:h-auto border-r border-slate-800 bg-slate-900/95 md:bg-slate-900/50 flex flex-col transition-all duration-300 ease-in-out overflow-hidden ${
          isSidebarOpen ? 'w-96 translate-x-0' : 'w-96 -translate-x-full md:w-0 md:translate-x-0 md:border-r-0'
        }`}
      >
        <div className="p-4 border-b border-slate-800 flex items-center justify-between min-w-[20rem]">
          <div>
            <h1
              className="text-xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent"
              title={buildInfoTooltip}
            >
              Tertius
            </h1>
            <p className="text-xs text-slate-500 mt-1">Open Source CAD Toolkit</p>
          </div>
          <button 
            className="md:hidden text-slate-400 hover:text-white"
            onClick={() => setIsSidebarOpen(false)}
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        
        <div className="flex-1 flex flex-col min-h-0 min-w-[20rem]">
          <ArtusWindow />
        </div>
      </div>

      {/* Main Workflow Viewport (Tabbed) */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* Tab Header */}
        {showImportBanner && (
          <div className="flex items-center gap-3 border-b border-cyan-900/50 bg-cyan-950/40 px-4 py-2 text-sm text-cyan-100">
            <span className="min-w-0 flex-1">
              Import your local guest draft into this account.
              {importError && <span className="ml-2 text-red-300">{importError}</span>}
            </span>
            <button
              type="button"
              onClick={handleImportGuestWorkspace}
              disabled={isImporting}
              className="rounded bg-cyan-600 px-3 py-1 font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isImporting ? 'Importing...' : 'Import'}
            </button>
            <button
              type="button"
              onClick={() => setShowImportBanner(false)}
              className="rounded px-2 py-1 text-cyan-200 hover:bg-cyan-900/60"
            >
              Dismiss
            </button>
          </div>
        )}
        <div className="flex bg-slate-900 border-b border-slate-800 px-4 pt-4 gap-2 overflow-x-auto whitespace-nowrap scrollbar-hide">
          <button 
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            className="p-2 mb-2 mr-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors shrink-0 flex items-center justify-center"
            title="Toggle Sidebar"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
          
          <button
            onClick={() => setActiveTab('generate')}
            className={`px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${activeTab === 'generate' ? 'bg-slate-950 text-cyan-300 font-medium border-slate-800' : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`}
          >
            Generate Design
          </button>
          <button 
            onClick={() => setActiveTab('extus')}
            className={`px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${activeTab === 'extus' ? 'bg-slate-950 text-cyan-300 font-medium border-slate-800' : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`}
          >
            👁️ Extus Viewport
          </button>
          <button 
            onClick={() => setActiveTab('intus')}
            className={`px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${activeTab === 'intus' ? 'bg-slate-950 text-indigo-300 font-medium border-slate-800' : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`}
          >
            ⚙️ Intus Compiler
          </button>
          <button 
            onClick={() => setActiveTab('timus')}
            className={`px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${activeTab === 'timus' ? 'bg-slate-950 text-emerald-300 font-medium border-slate-800' : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`}
          >
            📐 Timus Drafting
          </button>
          <button
            onClick={() => setActiveTab('octavus')}
            className={`px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${activeTab === 'octavus' ? 'bg-slate-950 text-amber-300 font-medium border-slate-800' : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`}
          >
            🛒 Procurement
          </button>
          <div className="ml-auto flex items-center space-x-2 mr-4">
            <div className="relative">
              <button 
                onClick={() => {
                  const el = document.getElementById('about-dropdown');
                  if (el) el.classList.toggle('hidden');
                }}
                className="px-3 py-2 mb-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors shrink-0"
              >
                About
              </button>
              <div id="about-dropdown" className="absolute right-0 top-full mt-2 w-64 bg-slate-800 rounded-lg shadow-xl border border-slate-700 hidden z-50">
                <div className="p-4 space-y-3 text-sm">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Frontend:</span>
                    <span className="text-slate-200 font-mono">v1.0.0</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Backend:</span>
                    <span className="text-slate-200 font-mono">v1.0.0</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Commit:</span>
                    <span className="text-slate-200 font-mono">{__GIT_COMMIT__}</span>
                  </div>
                  <div className="pt-2 border-t border-slate-700">
                    <a 
                      href="https://github.com/d-b-w-gain/Tertius-Web" 
                      target="_blank" 
                      rel="noopener noreferrer"
                      className="text-cyan-400 hover:text-cyan-300 flex items-center justify-between"
                    >
                      <span>View on GitHub</span>
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
                    </a>
                  </div>
                </div>
              </div>
            </div>
            <LoginStateWidget />
          </div>
        </div>

        <div className="flex-1 relative flex flex-col min-h-0 bg-slate-950">
          <div className={activeTab === 'generate' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
            <GenerateDesignWindow isActive={activeTab === 'generate'} />
          </div>
          <div className={activeTab === 'extus' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
            <ExtusWindow isActive={activeTab === 'extus'} />
          </div>
          <div className={activeTab === 'intus' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
            <IntusWindow isActive={activeTab === 'intus'} />
          </div>
          <div className={activeTab === 'timus' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
            <TimusWindow isActive={activeTab === 'timus'} />
          </div>
          <div className={activeTab === 'octavus' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
            <OctavusWindow isActive={activeTab === 'octavus'} onOpenCompiler={() => setActiveTab('intus')} />
          </div>
        </div>
      </div>
      <AiBudgetGauge serverUrl={intusServerUrl} />
    </div>
  )
}

export default App
