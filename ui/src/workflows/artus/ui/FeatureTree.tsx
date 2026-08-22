import React from 'react';
import * as THREE from 'three';
import type { SceneNodeAppearanceMap } from '../../shared/sceneNodeSelection';
import { isAssemblyTreeNode } from '../model/featureTree';
import { FeatureTreeNode } from './FeatureTreeNode';

export interface FeatureTreeProps {
  isActive: boolean;
  sceneGraph: THREE.Object3D | null;
  selectedValue: string | null;
  appearanceByPath: SceneNodeAppearanceMap;
  onActivate: () => void;
  onExportJson: () => void;
  onExportCsv: () => void;
  onSelect: (node: THREE.Object3D, isDouble: boolean) => void;
  onTarget: (node: THREE.Object3D) => void;
  onToggleVisibility: (node: THREE.Object3D) => void;
  onToggleTransparency: (node: THREE.Object3D) => void;
}

export const FeatureTree: React.FC<FeatureTreeProps> = ({
  isActive,
  sceneGraph,
  selectedValue,
  appearanceByPath,
  onActivate,
  onExportJson,
  onExportCsv,
  onSelect,
  onTarget,
  onToggleVisibility,
  onToggleTransparency,
}) => (
  <div className={`flex flex-col bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-lg transition-all ${isActive ? 'flex-1 min-h-0' : 'shrink-0'}`}>
    <div
      className="flex items-center justify-between p-3 border-b border-slate-800 bg-slate-900/50 shrink-0 cursor-pointer hover:bg-slate-800/80 transition-colors gap-3"
      onClick={onActivate}
    >
      <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
        <span className="text-sky-500">🧊</span> Assembly Tree
      </h2>
      <div className="flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
        <button
          onClick={onExportJson}
          disabled={!sceneGraph}
          className="text-xs px-3 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-slate-300 transition-colors"
          title="Export the current Assembly Tree candidate nodes as JSON"
        >
          Export JSON
        </button>
        <button
          onClick={onExportCsv}
          disabled={!sceneGraph}
          className="text-xs px-3 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-slate-300 transition-colors"
          title="Export the current Assembly Tree candidate nodes as CSV"
        >
          Export CSV
        </button>
      </div>
    </div>

    {isActive && (
      <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
        {!sceneGraph ? (
          <div className="text-slate-500 text-center py-8 text-sm">
            Waiting for 3D model...
          </div>
        ) : (
          <div className="flex flex-col ml-2">
            {sceneGraph.children.filter(isAssemblyTreeNode).map(child => (
              <FeatureTreeNode
                key={child.uuid}
                node={child}
                root={sceneGraph}
                depth={0}
                selectedValue={selectedValue}
                appearanceByPath={appearanceByPath}
                onSelect={onSelect}
                onTarget={onTarget}
                onToggleVisibility={onToggleVisibility}
                onToggleTransparency={onToggleTransparency}
              />
            ))}
          </div>
        )}
      </div>
    )}
  </div>
);
