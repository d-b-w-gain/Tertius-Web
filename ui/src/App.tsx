import { useEffect, useRef, useState } from 'react'
import { AppShell } from './app/AppShell'
import { GuestImportBanner } from './app/GuestImportBanner'
import { WorkbenchHost } from './app/WorkbenchHost'
import { WorkbenchNavigation, type WorkbenchTab } from './app/WorkbenchNavigation'
import { hasWorkbenchAccess, useAuth } from './auth/AuthProvider'
import { ArtusWindow } from './workflows/artus/ArtusWindow'
import { AiUsageGauge } from './workflows/generate/AiUsageGauge'
import { resolveWorkflowServerUrl } from './workflows/shared/apiConfig'
import { importGuestWorkspace } from './workflows/shared/guestImport'
import { GUEST_WORKSPACE_KEY } from './workflows/shared/guestWorkspace'

function App() {
  const { authMode, getAccessToken, isLoading, user } = useAuth()
  const [activeTab, setActiveTab] = useState<WorkbenchTab>('generate')
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [showImportBanner, setShowImportBanner] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [isImporting, setIsImporting] = useState(false)
  const previousAuthMode = useRef(authMode)
  const buildInfoTooltip = `Commit ${__GIT_COMMIT__}\nDate ${__GIT_COMMIT_DATE__}`
  const intusServerUrl = resolveWorkflowServerUrl('intus', import.meta.env?.VITE_API_URL)
  const canUseSiteWorkbench = hasWorkbenchAccess(user, 'site')
  const canUseStructuralWorkbench = hasWorkbenchAccess(user, 'structural')

  useEffect(() => {
    if (
      (activeTab === 'site' && !canUseSiteWorkbench) ||
      (activeTab === 'structural' && !canUseStructuralWorkbench)
    ) {
      setActiveTab('generate')
    }
  }, [activeTab, canUseSiteWorkbench, canUseStructuralWorkbench])

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
    <AppShell
      buildInfoTooltip={buildInfoTooltip}
      footer={<AiUsageGauge serverUrl={intusServerUrl} />}
      isSidebarOpen={isSidebarOpen}
      navigation={(
        <WorkbenchNavigation
          activeTab={activeTab}
          canUseSiteWorkbench={canUseSiteWorkbench}
          canUseStructuralWorkbench={canUseStructuralWorkbench}
          onSelectTab={setActiveTab}
          onToggleSidebar={() => setIsSidebarOpen((current) => !current)}
        />
      )}
      notification={showImportBanner ? (
        <GuestImportBanner
          importError={importError}
          isImporting={isImporting}
          onDismiss={() => setShowImportBanner(false)}
          onImport={handleImportGuestWorkspace}
        />
      ) : undefined}
      onCloseSidebar={() => setIsSidebarOpen(false)}
      sidebar={<ArtusWindow />}
    >
      <WorkbenchHost
        activeTab={activeTab}
        authMode={authMode}
        canUseSiteWorkbench={canUseSiteWorkbench}
        canUseStructuralWorkbench={canUseStructuralWorkbench}
        onSelectTab={setActiveTab}
      />
    </AppShell>
  )
}

export default App
