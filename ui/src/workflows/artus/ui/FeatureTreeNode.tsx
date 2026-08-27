import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import {
  type SceneNodeAppearanceMap,
  getSceneNodePathKey,
  isSceneNodeSelectionMatch,
} from '../../shared/sceneNodeSelection';
import { isAssemblyTreeNode } from '../model/featureTree';

const EyeIcon: React.FC = () => (
  <svg aria-hidden="true" viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" />
    <circle cx="12" cy="12" r="2.5" />
  </svg>
);

const EyeOffIcon: React.FC = () => (
  <svg aria-hidden="true" viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9.9 5.2A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a18.4 18.4 0 0 1-3 4.1" />
    <path d="M14.1 14.1A3 3 0 0 1 9.9 9.9" />
    <path d="M2 2l20 20" />
    <path d="M6.4 6.4C3.6 8.3 2 12 2 12s3.5 7 10 7a10.5 10.5 0 0 0 5.6-1.6" />
  </svg>
);

const TransparencyIcon: React.FC<{ active: boolean }> = ({ active }) => (
  <svg aria-hidden="true" viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="8" />
    <path d="M12 4v16" />
    {active && <path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor" opacity="0.45" stroke="none" />}
  </svg>
);

const TargetIcon: React.FC = () => (
  <svg aria-hidden="true" viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="7" />
    <path d="M12 3v3" />
    <path d="M12 18v3" />
    <path d="M3 12h3" />
    <path d="M18 12h3" />
    <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
  </svg>
);

export interface FeatureTreeNodeProps {
  node: THREE.Object3D;
  root: THREE.Object3D;
  depth: number;
  selectedValue: string | null;
  appearanceByPath: SceneNodeAppearanceMap;
  onSelect: (node: THREE.Object3D, isDouble: boolean) => void;
  onTarget: (node: THREE.Object3D) => void;
  onToggleVisibility: (node: THREE.Object3D) => void;
  onToggleTransparency: (node: THREE.Object3D) => void;
}

export const FeatureTreeNode: React.FC<FeatureTreeNodeProps> = ({
  node,
  root,
  depth,
  selectedValue,
  appearanceByPath,
  onSelect,
  onTarget,
  onToggleVisibility,
  onToggleTransparency,
}) => {
  const [expanded, setExpanded] = useState(depth < 2);
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const nodeRef = useRef<HTMLDivElement>(null);

  const isMesh = (node as THREE.Mesh).isMesh;
  const isGroup = node.type === 'Group' || node.type === 'Object3D';
  const visibleChildren = useMemo(() => node.children.filter(isAssemblyTreeNode), [node.children]);
  const hasChildren = visibleChildren.length > 0;
  const displayName = node.name || (isMesh ? 'Mesh' : 'Component');
  const isSelected = isSceneNodeSelectionMatch(root, node, selectedValue);
  const containsSelectedNode = useMemo(() => {
    let containsSelection = false;
    node.traverse((child) => {
      if (isSceneNodeSelectionMatch(root, child, selectedValue)) containsSelection = true;
    });
    return containsSelection;
  }, [node, root, selectedValue]);
  const nodePathKey = getSceneNodePathKey(root, node);
  const appearance = appearanceByPath[nodePathKey] || {};
  const isHidden = appearance.hidden === true;
  const isTransparent = appearance.transparent === true;
  const showControls = isHovered || isFocused || isHidden || isTransparent;

  useEffect(() => {
    if (containsSelectedNode) setExpanded(true);
  }, [containsSelectedNode]);

  useEffect(() => {
    if (!isSelected) return;
    const frame = requestAnimationFrame(() => {
      nodeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
    return () => cancelAnimationFrame(frame);
  }, [isSelected]);

  if (!isAssemblyTreeNode(node) || (!isMesh && !isGroup)) return null;

  return (
    <div ref={nodeRef} className="flex flex-col font-mono text-xs">
       <div
         className={`flex w-full items-center py-0.5 px-2 cursor-pointer transition-colors ${isSelected ? 'bg-indigo-900/40 border border-indigo-500/50 rounded shadow-[inset_0_0_10px_rgba(99,102,241,0.2)]' : 'hover:bg-slate-800/50'}`}
         style={{ paddingLeft: `${depth * 16 + 8}px` }}
         onMouseEnter={() => setIsHovered(true)}
         onMouseLeave={() => setIsHovered(false)}
         onFocus={() => setIsFocused(true)}
         onBlur={() => setIsFocused(false)}
         onClick={() => onSelect(node, false)}
         onDoubleClick={() => onSelect(node, true)}
       >
          {hasChildren ? (
            <span onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }} className="w-4 mr-1 text-[10px] text-slate-500 hover:text-slate-300 focus:outline-none flex-shrink-0 flex items-center justify-center opacity-70">
              {expanded ? '▼' : '▶'}
            </span>
          ) : <span className="w-4 mr-1 inline-block" />}
          <span className={`min-w-0 flex-1 text-xs font-medium truncate select-none ${isHidden ? 'text-slate-500 line-through decoration-slate-600' : isTransparent ? 'text-slate-400' : 'text-slate-300'}`}>{displayName}</span>
          <div className={`ml-2 flex shrink-0 items-center gap-1 transition-opacity duration-150 ${showControls ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}>
            <button
              type="button"
              title="Frame component in the 3D viewer"
              aria-label={`Frame ${displayName}`}
              className="flex h-5 w-5 items-center justify-center rounded border border-slate-700 bg-slate-950 text-slate-300 transition-colors hover:border-indigo-500 hover:text-indigo-300"
              onClick={(e) => {
                e.stopPropagation();
                onTarget(node);
              }}
            >
              <TargetIcon />
            </button>
            <button
              type="button"
              title={isHidden ? 'Show component and children' : 'Hide component and children'}
              aria-label={isHidden ? `Show ${displayName}` : `Hide ${displayName}`}
              className={`flex h-5 w-5 items-center justify-center rounded border transition-colors ${isHidden ? 'border-slate-600 bg-slate-900 text-slate-500 hover:text-slate-200' : 'border-slate-700 bg-slate-950 text-slate-300 hover:border-sky-500 hover:text-sky-300'}`}
              onClick={(e) => {
                e.stopPropagation();
                onToggleVisibility(node);
              }}
            >
              {isHidden ? <EyeOffIcon /> : <EyeIcon />}
            </button>
            <button
              type="button"
              title={isTransparent ? 'Make opaque' : 'Make transparent'}
              aria-label={isTransparent ? `Make ${displayName} opaque` : `Make ${displayName} transparent`}
              className={`flex h-5 w-5 items-center justify-center rounded border transition-colors ${isTransparent ? 'border-cyan-500 bg-cyan-950/50 text-cyan-300' : 'border-slate-700 bg-slate-950 text-slate-300 hover:border-cyan-500 hover:text-cyan-300'}`}
              onClick={(e) => {
                e.stopPropagation();
                onToggleTransparency(node);
              }}
            >
              <TransparencyIcon active={isTransparent} />
            </button>
          </div>
       </div>
       {expanded && hasChildren && visibleChildren.map(c => (
         <div key={c.uuid} className="flex flex-col relative">
           <div
             className="absolute left-0 top-0 bottom-0 w-px bg-slate-800/50"
             style={{ marginLeft: `${depth * 16 + 14}px` }}
           />
           <FeatureTreeNode
             node={c}
             root={root}
             depth={depth + 1}
             selectedValue={selectedValue}
             appearanceByPath={appearanceByPath}
             onSelect={onSelect}
             onTarget={onTarget}
             onToggleVisibility={onToggleVisibility}
             onToggleTransparency={onToggleTransparency}
           />
         </div>
       ))}
    </div>
  );
};
