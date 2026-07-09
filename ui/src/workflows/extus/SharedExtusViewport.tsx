import React from 'react'
import { useAuth } from '../../auth/AuthProvider'
import { useServerLauncher } from './ui/ServerLauncher/useServerLauncher'
import { LatestModelViewer, ModelViewerCanvas } from './ui/ViewerTab'
import type { ComponentPreviewImage } from '../shared/componentPreview'

export type SharedExtusViewportSource =
  | {
      kind: 'latest'
      statusTextOverride?: string
    }
  | {
      kind: 'artifact'
      modelUrl: string
      statusText?: string
      projectName?: string
    }

export const SharedExtusViewport: React.FC<{
  isActive: boolean
  source: SharedExtusViewportSource
  externalSelectedNodeIds?: string[]
  onExternalSelectionPreviewChange?: (preview: ComponentPreviewImage | null) => void
}> = ({ isActive, source, externalSelectedNodeIds, onExternalSelectionPreviewChange }) => {
  const { getAccessToken } = useAuth()
  const server = useServerLauncher({
    workflowFolder: 'tertius/extus',
    scriptName: 'extus_server.py',
    port: 8892,
    serverName: 'extus-viewer',
    packages: ['fastapi', 'uvicorn[standard]'],
  })

  if (source.kind === 'artifact') {
    return (
      <ModelViewerCanvas
        key={source.modelUrl}
        modelUrl={source.modelUrl}
        getAccessToken={getAccessToken}
        statusText={source.statusText || 'Selected historical model'}
        projectName={source.projectName}
        isActive={isActive}
        externalSelectedNodeIds={externalSelectedNodeIds}
        onExternalSelectionPreviewChange={onExternalSelectionPreviewChange}
      />
    )
  }

  return (
    <LatestModelViewer
      serverUrl={server.serverUrl}
      isActive={isActive}
      statusTextOverride={source.statusTextOverride}
      externalSelectedNodeIds={externalSelectedNodeIds}
      onExternalSelectionPreviewChange={onExternalSelectionPreviewChange}
    />
  )
}
