import React from 'react';
import { resolveWorkflowServerUrl } from '../shared/apiConfig';
import { BomReviewTab } from './ui/BomReviewTab';

export const OctavusWindow: React.FC<{
  isActive?: boolean;
  onOpenCompiler?: () => void;
  useSharedViewport?: boolean;
  onViewportSelectionChange?: (visualNodeIds: string[]) => void;
  onViewportFrameChange?: (rect: DOMRectReadOnly | null) => void;
}> = ({ isActive = true, onOpenCompiler, useSharedViewport = false, onViewportSelectionChange, onViewportFrameChange }) => {
  const artusServerUrl = resolveWorkflowServerUrl('artus', import.meta.env?.VITE_API_URL);
  const extusServerUrl = resolveWorkflowServerUrl('extus', import.meta.env?.VITE_API_URL);
  const intusServerUrl = resolveWorkflowServerUrl('intus', import.meta.env?.VITE_API_URL);

  return (
    <div className={`flex h-full flex-col text-slate-100 font-sans ${useSharedViewport ? 'bg-transparent' : 'bg-slate-950'}`}>
      <BomReviewTab
        artusServerUrl={artusServerUrl}
        extusServerUrl={extusServerUrl}
        intusServerUrl={intusServerUrl}
        isActive={isActive}
        onOpenCompiler={onOpenCompiler}
        useSharedViewport={useSharedViewport}
        onViewportSelectionChange={onViewportSelectionChange}
        onViewportFrameChange={onViewportFrameChange}
      />
    </div>
  );
};
