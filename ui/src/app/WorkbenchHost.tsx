import { useCallback, useEffect, useRef, useState } from 'react'
import { SharedExtusViewport, type SharedExtusViewportSource } from '../workflows/extus/SharedExtusViewport'
import { GenerateDesignWindow, type GenerateViewportState } from '../workflows/generate/GenerateDesignWindow'
import { IntusWindow } from '../workflows/intus/IntusWindow'
import { OctavusWindow } from '../workflows/octavus/OctavusWindow'
import { SiteWorkbench } from '../workflows/site/SiteWorkbench'
import { StructuralWorkbench } from '../workflows/structural/StructuralWorkbench'
import { TimusWindow } from '../workflows/timus/TimusWindow'
import type { ComponentPreviewImage } from '../workflows/shared/componentPreview'
import type { WorkbenchTab } from './WorkbenchNavigation'

type ViewportFrame = { left: number; top: number; width: number; height: number }

type WorkbenchHostProps = {
  activeTab: WorkbenchTab
  authMode: 'guest' | 'authenticated'
  canUseSiteWorkbench: boolean
  canUseStructuralWorkbench: boolean
  onSelectTab: (tab: WorkbenchTab) => void
}

export function WorkbenchHost({ activeTab, authMode, canUseSiteWorkbench, canUseStructuralWorkbench, onSelectTab }: WorkbenchHostProps) {
  const [procurementSelectedVisualNodeIds, setProcurementSelectedVisualNodeIds] = useState<string[]>([])
  const [procurementComponentPreview, setProcurementComponentPreview] = useState<ComponentPreviewImage | null>(null)
  const [procurementViewportFrame, setProcurementViewportFrame] = useState<ViewportFrame | null>(null)
  const [generateViewportState, setGenerateViewportState] = useState<GenerateViewportState>({ title: 'Latest Model', subtitle: 'No active project', projectName: '', modelUrl: '' })
  const workbenchRef = useRef<HTMLDivElement | null>(null)
  const usesSharedExtusViewport = activeTab === 'extus' || activeTab === 'octavus' || (activeTab === 'generate' && authMode !== 'guest')
  const sharedExtusViewportSource: SharedExtusViewportSource = activeTab === 'generate' && generateViewportState.modelUrl
    ? { kind: 'artifact', modelUrl: generateViewportState.modelUrl, projectName: generateViewportState.projectName, statusText: generateViewportState.statusText }
    : { kind: 'latest', statusTextOverride: activeTab === 'generate' ? generateViewportState.statusText : undefined }

  const handleProcurementViewportFrameChange = useCallback((rect: DOMRectReadOnly | null) => {
    const host = workbenchRef.current?.getBoundingClientRect()
    if (!rect || !host || rect.width <= 0 || rect.height <= 0) {
      setProcurementViewportFrame(null)
      return
    }
    const next = { left: Math.max(0, rect.left - host.left), top: Math.max(0, rect.top - host.top), width: rect.width, height: rect.height }
    setProcurementViewportFrame((current) => current && Math.abs(current.left - next.left) < 0.5 && Math.abs(current.top - next.top) < 0.5 && Math.abs(current.width - next.width) < 0.5 && Math.abs(current.height - next.height) < 0.5 ? current : next)
  }, [])

  const sharedViewportFrameClass = activeTab === 'octavus' ? procurementViewportFrame ? 'absolute flex flex-col overflow-hidden bg-slate-950' : 'hidden' : 'absolute inset-0 flex flex-col'
  const sharedViewportFrameStyle = activeTab === 'octavus' && procurementViewportFrame ? { left: `${procurementViewportFrame.left}px`, top: `${procurementViewportFrame.top}px`, width: `${procurementViewportFrame.width}px`, height: `${procurementViewportFrame.height}px` } : undefined

  useEffect(() => {
    if (activeTab !== 'octavus') setProcurementViewportFrame(null)
  }, [activeTab])

  return (
    <div ref={workbenchRef} className="flex-1 relative flex flex-col min-h-0 bg-slate-950">
      {usesSharedExtusViewport && <div className={sharedViewportFrameClass} style={sharedViewportFrameStyle}>
        <SharedExtusViewport isActive={usesSharedExtusViewport} source={sharedExtusViewportSource} externalSelectedNodeIds={activeTab === 'octavus' ? procurementSelectedVisualNodeIds : undefined} onExternalSelectionPreviewChange={activeTab === 'octavus' ? setProcurementComponentPreview : undefined} />
      </div>}
      <div className={activeTab === 'generate' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
        <GenerateDesignWindow isActive={activeTab === 'generate'} renderViewport={false} onViewportStateChange={setGenerateViewportState} />
      </div>
      <div className={activeTab === 'intus' ? 'absolute inset-0 flex flex-col' : 'hidden'}><IntusWindow isActive={activeTab === 'intus'} /></div>
      <div className={activeTab === 'timus' ? 'absolute inset-0 flex flex-col' : 'hidden'}><TimusWindow isActive={activeTab === 'timus'} /></div>
      <div className={activeTab === 'octavus' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
        <OctavusWindow isActive={activeTab === 'octavus'} onOpenCompiler={() => onSelectTab('intus')} useSharedViewport onViewportSelectionChange={setProcurementSelectedVisualNodeIds} onViewportFrameChange={handleProcurementViewportFrameChange} componentPreviewImage={procurementComponentPreview} />
      </div>
      {canUseStructuralWorkbench && <div className={activeTab === 'structural' ? 'absolute inset-0 flex flex-col' : 'hidden'}><StructuralWorkbench isActive={activeTab === 'structural'} /></div>}
      {canUseSiteWorkbench && <div className={activeTab === 'site' ? 'absolute inset-0 flex flex-col' : 'hidden'}><SiteWorkbench isActive={activeTab === 'site'} /></div>}
    </div>
  )
}
