export type StructuralViewerOverlay = {
  id: string;
  label: string;
  mode?: 'moment' | 'displacement';
  status?: 'pass' | 'fail' | 'not_checked';
  utilisation?: number | null;
  diagramColor?: number;
  stations: Array<{
    position: { x: number; y: number; z: number };
    moment_kNm?: { x: number; y: number; z: number };
    displacement_mm?: { x: number; y: number; z: number };
  }>;
  loadArrows?: Array<{
    id: string;
    label: string;
    position: { x: number; y: number; z: number };
    force_kN: { x: number; y: number; z: number };
  }>;
  nodes?: Array<{
    id: string;
    label: string;
    position: { x: number; y: number; z: number };
    restrained: boolean;
  }>;
  reactions?: Array<{
    id: string;
    label: string;
    position: { x: number; y: number; z: number };
    force_kN: { x: number; y: number; z: number };
    moment_kNm: { x: number; y: number; z: number };
  }>;
  restraintSegments?: Array<{
    id: string;
    label: string;
    start: { x: number; y: number; z: number };
    end: { x: number; y: number; z: number };
    compressionFlange: 'positive_local_y' | 'negative_local_y' | 'none';
    status: 'missing' | 'candidate' | 'inadequate' | 'verified' | 'not_required';
  }>;
  maxOffsetMm?: number;
};
