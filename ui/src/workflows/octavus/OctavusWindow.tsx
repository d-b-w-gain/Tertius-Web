import React from 'react';
import { resolveWorkflowServerUrl } from '../shared/apiConfig';
import { BomReviewTab } from './ui/BomReviewTab';

export const OctavusWindow: React.FC<{
  isActive?: boolean;
  onOpenCompiler?: () => void;
}> = ({ isActive = true, onOpenCompiler }) => {
  const artusServerUrl = resolveWorkflowServerUrl('artus', import.meta.env?.VITE_API_URL);
  const extusServerUrl = resolveWorkflowServerUrl('extus', import.meta.env?.VITE_API_URL);
  const intusServerUrl = resolveWorkflowServerUrl('intus', import.meta.env?.VITE_API_URL);

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 font-sans">
      <BomReviewTab
        artusServerUrl={artusServerUrl}
        extusServerUrl={extusServerUrl}
        intusServerUrl={intusServerUrl}
        isActive={isActive}
        onOpenCompiler={onOpenCompiler}
      />
    </div>
  );
};
