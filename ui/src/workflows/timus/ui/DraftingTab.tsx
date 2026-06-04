import React, { useState, useEffect } from 'react';

const INTUS_URL = 'http://localhost:8891';

export type ArrangementType = 'combined' | 'top' | 'north' | 'east';
export type SheetSize = 'A4' | 'A3' | 'A2' | 'A1' | 'A0';
export type Orientation = 'landscape' | 'portrait';

const PAPER_SIZES: Record<SheetSize, { width: number, height: number }> = {
  'A4': { width: 297, height: 210 },
  'A3': { width: 420, height: 297 },
  'A2': { width: 594, height: 420 },
  'A1': { width: 841, height: 594 },
  'A0': { width: 1189, height: 841 },
};

export const DraftingTab: React.FC<{ serverUrl: string }> = ({ serverUrl }) => {
  const [activeProject, setActiveProject] = useState<string>('');
  const [refreshKey, setRefreshKey] = useState(0);
  
  const [arrangement, setArrangement] = useState<ArrangementType>('combined');
  const [sheetSize, setSheetSize] = useState<SheetSize>('A3');
  const [orientation, setOrientation] = useState<Orientation>('landscape');

  const [autoScales, setAutoScales] = useState<{combined: number, top: number, north: number, east: number} | null>(null);

  const [selectedElement, setSelectedElement] = useState<'top' | 'north' | 'east' | 'title' | 'sheet' | null>(null);
  const [customScales, setCustomScales] = useState<{top?: number, north?: number, east?: number}>({});
  const [titleOverrides, setTitleOverrides] = useState({
    project: '',
    title: 'GENERAL ARRANGEMENT',
    date: new Date().toISOString().split('T')[0]
  });

  const getSheetDimensions = () => {
    const dims = PAPER_SIZES[sheetSize];
    if (orientation === 'landscape') return { w: Math.max(dims.width, dims.height), h: Math.min(dims.width, dims.height) };
    return { w: Math.min(dims.width, dims.height), h: Math.max(dims.width, dims.height) };
  };

  const { w: sheetW, h: sheetH } = getSheetDimensions();

  const getScale = (view: 'top' | 'north' | 'east') => {
    if (customScales[view]) return customScales[view];
    if (!autoScales) return null;
    return arrangement === 'combined' ? autoScales.combined : autoScales[view];
  };

  const getActiveScaleDisplay = () => {
    if (Object.keys(customScales).length > 0) return 'AS NOTED';
    if (!autoScales) return 'TO FIT';
    if (arrangement === 'combined') return `${autoScales.combined}:1`;
    return `${autoScales[arrangement]}:1`;
  };

  useEffect(() => {
    const fetchActive = async () => {
      try {
        const res = await fetch(`${INTUS_URL}/projects`);
        if (res.ok) {
           const data = await res.json();
           const projects = data.projects || [];
           const last = localStorage.getItem('intus_last_project');
           if (last && projects.includes(last)) setActiveProject(last);
           else if (projects.length > 0) setActiveProject(projects[0]);
        }
      } catch (e) {
        console.error("Failed to fetch projects");
      }
    };
    fetchActive();
  }, []);

  useEffect(() => {
    if (!activeProject) return;
    let mtime = 0;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${INTUS_URL}/projects/${activeProject}/status`);
        if (res.ok) {
          const data = await res.json();
          if (data.mtime && mtime !== 0 && data.mtime > mtime) {
            setRefreshKey(k => k + 1);
          }
          if (data.mtime) mtime = data.mtime;
        }
      } catch (e) {}
    }, 1000);
    return () => clearInterval(interval);
  }, [activeProject]);

  useEffect(() => {
    if (!activeProject) return;
    const fetchBounds = async () => {
      try {
        const res = await fetch(`${serverUrl}/projects/${activeProject}/bounds`);
        if (res.ok) {
           const bounds = await res.json();
           const { dx, dy, dz } = bounds;
           const padding = 0.8;
           
           const paddingMm = 20; 
           const workW = sheetW - paddingMm;
           const workH = sheetH - paddingMm;
           
           let c_min = 1;
           if (orientation === 'landscape') {
             const c_topX = workW / dx; const c_topY = (workH/2) / dy;
             const c_frontX = (workW/2) / dx; const c_frontY = (workH/2) / dz;
             const c_sideX = (workW/2) / dy; const c_sideY = (workH/2) / dz;
             c_min = Math.min(c_topX, c_topY, c_frontX, c_frontY, c_sideX, c_sideY) * padding;
           } else {
             const c_topX = workW / dx; const c_topY = (workH/3) / dy;
             const c_frontX = workW / dx; const c_frontY = (workH/3) / dz;
             const c_sideX = workW / dy; const c_sideY = (workH/3) / dz;
             c_min = Math.min(c_topX, c_topY, c_frontX, c_frontY, c_sideX, c_sideY) * padding;
           }
           
           let i_top = Math.min(workW / dx, workH / dy) * padding;
           let i_front = Math.min(workW / dx, workH / dz) * padding;
           let i_side = Math.min(workW / dy, workH / dz) * padding;

           const roundScale = (s: number) => {
             if (s > 1) return Math.floor(s);
             return Number(s.toPrecision(1));
           };

           setAutoScales({
             combined: roundScale(c_min),
             top: roundScale(i_top),
             north: roundScale(i_front),
             east: roundScale(i_side)
           });
        }
      } catch (e) {}
    };
    fetchBounds();
  }, [activeProject, refreshKey, serverUrl, sheetW, sheetH, orientation]);

  useEffect(() => {
    if (arrangement === 'combined') setTitleOverrides(prev => ({...prev, title: 'GENERAL ARRANGEMENT'}));
    if (arrangement === 'top') setTitleOverrides(prev => ({...prev, title: 'TOP VIEW DETAILS'}));
    if (arrangement === 'north') setTitleOverrides(prev => ({...prev, title: 'FRONT ELEVATION DETAILS'}));
    if (arrangement === 'east') setTitleOverrides(prev => ({...prev, title: 'SIDE ELEVATION DETAILS'}));
  }, [arrangement]);

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="w-full h-full bg-slate-200 overflow-auto p-8 flex justify-center items-center font-sans text-slate-900 relative">
      <style>
        {`
          @media print {
            body * { visibility: hidden; }
            .print-area, .print-area * { visibility: visible; }
            .print-area { position: absolute !important; left: 0 !important; top: 0 !important; margin: 0 !important; padding: 0 !important; border: none !important; box-shadow: none !important; }
            .no-print { display: none !important; }
            @page { size: ${sheetSize} ${orientation}; margin: 0; }
          }
        `}
      </style>

      {/* Floating Controls */}
      <div className="absolute top-4 left-4 flex gap-4 z-50 no-print items-center">
        <select 
          className="border border-slate-400 rounded px-3 py-2 text-sm bg-white font-bold"
          value={arrangement}
          onChange={e => setArrangement(e.target.value as ArrangementType)}
        >
          <option value="combined">Combined Views Sheet</option>
          <option value="top">Top View Sheet</option>
          <option value="north">Front Elevation Sheet</option>
          <option value="east">Side Elevation Sheet</option>
        </select>
      </div>

      <div className="absolute top-4 right-4 flex gap-4 z-50 no-print">
        <button onClick={handlePrint} className="bg-slate-800 text-white px-4 py-2 rounded shadow hover:bg-slate-700 font-bold text-sm">
          Export PDF (Print)
        </button>
      </div>

      {/* Floating Properties Panel */}
      {selectedElement && (
        <div className="absolute top-16 right-4 w-72 bg-white border border-slate-300 shadow-xl rounded-lg p-4 z-50 no-print">
          <div className="flex justify-between items-center mb-4 border-b pb-2">
            <h3 className="font-bold text-sm uppercase text-slate-600">
              {selectedElement === 'title' ? 'Title Block' : selectedElement === 'sheet' ? 'Sheet' : `${selectedElement} View`} Properties
            </h3>
            <button onClick={() => setSelectedElement(null)} className="text-slate-400 hover:text-slate-800">&times;</button>
          </div>
          
          {selectedElement === 'sheet' && (
            <div className="flex flex-col gap-3">
              <div>
                <label className="text-xs font-bold text-slate-500 uppercase block mb-1">Sheet Size</label>
                <select 
                  className="border rounded p-2 text-sm w-full bg-white"
                  value={sheetSize}
                  onChange={e => setSheetSize(e.target.value as SheetSize)}
                >
                  {Object.keys(PAPER_SIZES).map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs font-bold text-slate-500 uppercase block mb-1">Orientation</label>
                <select 
                  className="border rounded p-2 text-sm w-full bg-white"
                  value={orientation}
                  onChange={e => setOrientation(e.target.value as Orientation)}
                >
                  <option value="landscape">Landscape</option>
                  <option value="portrait">Portrait</option>
                </select>
              </div>
            </div>
          )}

          {selectedElement !== 'title' && selectedElement !== 'sheet' && (
            <div className="flex flex-col gap-2">
              <label className="text-xs font-bold text-slate-500 uppercase">Custom Scale Override</label>
              <input 
                type="number" 
                step="0.01" 
                className="border rounded p-2 text-sm w-full"
                placeholder={`Auto`}
                value={customScales[selectedElement as 'top'|'north'|'east'] || ''}
                onChange={e => {
                  const val = parseFloat(e.target.value);
                  setCustomScales({...customScales, [selectedElement]: isNaN(val) ? undefined : val});
                }}
              />
              <p className="text-[10px] text-slate-400">Clear to use auto scale.</p>
            </div>
          )}

          {selectedElement === 'title' && (
            <div className="flex flex-col gap-3">
              <div>
                <label className="text-xs font-bold text-slate-500 uppercase block mb-1">Project Name</label>
                <input 
                  type="text" 
                  className="border rounded p-2 text-sm w-full"
                  placeholder={activeProject || 'NO PROJECT'}
                  value={titleOverrides.project}
                  onChange={e => setTitleOverrides({...titleOverrides, project: e.target.value})}
                />
              </div>
              <div>
                <label className="text-xs font-bold text-slate-500 uppercase block mb-1">Drawing Title</label>
                <input 
                  type="text" 
                  className="border rounded p-2 text-sm w-full"
                  value={titleOverrides.title}
                  onChange={e => setTitleOverrides({...titleOverrides, title: e.target.value})}
                />
              </div>
              <div>
                <label className="text-xs font-bold text-slate-500 uppercase block mb-1">Date</label>
                <input 
                  type="date" 
                  className="border rounded p-2 text-sm w-full"
                  value={titleOverrides.date}
                  onChange={e => setTitleOverrides({...titleOverrides, date: e.target.value})}
                />
              </div>
            </div>
          )}
        </div>
      )}
      
      {/* The Sheet */}
      <div 
        className={`bg-white shadow-2xl relative shrink-0 print-area cursor-pointer transition-all ${selectedElement === 'sheet' ? 'ring-4 ring-blue-400 ring-offset-4' : ''}`} 
        style={{ width: `${sheetW}mm`, height: `${sheetH}mm` }}
        onClick={(e) => {
          if (e.target === e.currentTarget) setSelectedElement('sheet');
        }}
      >
        {/* Coordinate Border */}
        <div className="border-2 border-black flex pointer-events-none" style={{ position: 'absolute', top: '10mm', right: '10mm', bottom: '10mm', left: '10mm' }}>
          
          {/* Top Border Cells */}
          <div className="border-b-2 border-black flex" style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '10mm' }}>
            {['1','2','3','4','5','6','7','8'].map((n, i) => (
              <div key={n} className={`flex-1 flex items-center justify-center font-bold text-sm ${i < 7 ? 'border-r-2 border-black' : ''}`}>{n}</div>
            ))}
          </div>
          {/* Bottom Border Cells */}
          <div className="border-t-2 border-black flex" style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: '10mm' }}>
            {['1','2','3','4','5','6','7','8'].map((n, i) => (
              <div key={n} className={`flex-1 flex items-center justify-center font-bold text-sm ${i < 7 ? 'border-r-2 border-black' : ''}`}>{n}</div>
            ))}
          </div>
          {/* Left Border Cells */}
          <div className="flex flex-col" style={{ position: 'absolute', top: 0, bottom: 0, left: 0, width: '10mm', paddingTop: '10mm', paddingBottom: '10mm' }}>
            {['A','B','C','D','E','F'].map((c, i) => (
              <div key={c} className={`flex-1 flex items-center justify-center font-bold text-sm ${i < 5 ? 'border-b-2 border-black' : ''}`}>{c}</div>
            ))}
          </div>
          {/* Right Border Cells */}
          <div className="border-l-2 border-black flex flex-col" style={{ position: 'absolute', top: 0, bottom: 0, right: 0, width: '10mm', paddingTop: '10mm', paddingBottom: '10mm' }}>
            {['A','B','C','D','E','F'].map((c, i) => (
              <div key={c} className={`flex-1 flex items-center justify-center font-bold text-sm ${i < 5 ? 'border-b-2 border-black' : ''}`}>{c}</div>
            ))}
          </div>
        </div>

        {/* Viewports Container */}
        <div className="flex" style={{ position: 'absolute', top: '10mm', right: '10mm', bottom: '10mm', left: '10mm', padding: '10mm', flexDirection: orientation === 'portrait' ? 'column' : 'column' }}>
          
          {arrangement === 'combined' && orientation === 'landscape' && (
            <>
              <div className="flex-1 flex justify-center items-center pb-4 min-h-0">
                {activeProject && (
                  <div 
                      className={`relative w-full h-full border overflow-hidden cursor-pointer transition-colors ${selectedElement === 'top' ? 'border-blue-500 border-2' : 'border-dashed border-slate-300'}`}
                      onClick={() => setSelectedElement('top')}
                  >
                      <div className="absolute top-2 left-2 bg-white px-2 text-xs font-bold font-mono text-slate-800 z-10 flex gap-2 items-center">
                        <span>TOP VIEW</span>
                        {customScales.top && <span className="text-slate-500 text-[10px]">SCALE {customScales.top}:1</span>}
                      </div>
                      <img 
                        src={`${serverUrl}/projects/${activeProject}/views/top.svg?t=${refreshKey}${getScale('top') ? `&scale=${getScale('top')}` : ''}`} 
                        className="w-full h-full object-none"
                        alt="Top View"
                      />
                  </div>
                )}
              </div>

              <div className="flex-1 flex gap-4 min-h-0">
                <div 
                  className={`flex-1 relative border overflow-hidden cursor-pointer transition-colors ${selectedElement === 'north' ? 'border-blue-500 border-2' : 'border-dashed border-slate-300'}`}
                  onClick={() => setSelectedElement('north')}
                >
                  <div className="absolute top-2 left-2 bg-white px-2 text-xs font-bold font-mono text-slate-800 z-10 flex gap-2 items-center">
                    <span>FRONT ELEVATION (NORTH)</span>
                    {customScales.north && <span className="text-slate-500 text-[10px]">SCALE {customScales.north}:1</span>}
                  </div>
                  {activeProject && (
                    <img 
                        src={`${serverUrl}/projects/${activeProject}/views/north.svg?t=${refreshKey}${getScale('north') ? `&scale=${getScale('north')}` : ''}`} 
                        className="w-full h-full object-none"
                        alt="North View"
                      />
                  )}
                </div>
                <div 
                  className={`flex-1 relative border overflow-hidden cursor-pointer transition-colors ${selectedElement === 'east' ? 'border-blue-500 border-2' : 'border-dashed border-slate-300'}`}
                  onClick={() => setSelectedElement('east')}
                >
                  <div className="absolute top-2 left-2 bg-white px-2 text-xs font-bold font-mono text-slate-800 z-10 flex gap-2 items-center">
                    <span>SIDE ELEVATION (EAST)</span>
                    {customScales.east && <span className="text-slate-500 text-[10px]">SCALE {customScales.east}:1</span>}
                  </div>
                  {activeProject && (
                    <img 
                        src={`${serverUrl}/projects/${activeProject}/views/east.svg?t=${refreshKey}${getScale('east') ? `&scale=${getScale('east')}` : ''}`} 
                        className="w-full h-full object-none"
                        alt="East View"
                      />
                  )}
                </div>
              </div>
            </>
          )}

          {arrangement === 'combined' && orientation === 'portrait' && (
            <>
              <div className="flex-1 flex justify-center items-center pb-4 min-h-0">
                {activeProject && (
                  <div 
                      className={`relative w-full h-full border overflow-hidden cursor-pointer transition-colors ${selectedElement === 'top' ? 'border-blue-500 border-2' : 'border-dashed border-slate-300'}`}
                      onClick={() => setSelectedElement('top')}
                  >
                      <div className="absolute top-2 left-2 bg-white px-2 text-xs font-bold font-mono text-slate-800 z-10 flex gap-2 items-center">
                        <span>TOP VIEW</span>
                        {customScales.top && <span className="text-slate-500 text-[10px]">SCALE {customScales.top}:1</span>}
                      </div>
                      <img 
                        src={`${serverUrl}/projects/${activeProject}/views/top.svg?t=${refreshKey}${getScale('top') ? `&scale=${getScale('top')}` : ''}`} 
                        className="w-full h-full object-none"
                        alt="Top View"
                      />
                  </div>
                )}
              </div>
              <div className="flex-1 flex justify-center items-center pb-4 min-h-0">
                {activeProject && (
                  <div 
                      className={`relative w-full h-full border overflow-hidden cursor-pointer transition-colors ${selectedElement === 'north' ? 'border-blue-500 border-2' : 'border-dashed border-slate-300'}`}
                      onClick={() => setSelectedElement('north')}
                  >
                      <div className="absolute top-2 left-2 bg-white px-2 text-xs font-bold font-mono text-slate-800 z-10 flex gap-2 items-center">
                        <span>FRONT ELEVATION (NORTH)</span>
                        {customScales.north && <span className="text-slate-500 text-[10px]">SCALE {customScales.north}:1</span>}
                      </div>
                      <img 
                        src={`${serverUrl}/projects/${activeProject}/views/north.svg?t=${refreshKey}${getScale('north') ? `&scale=${getScale('north')}` : ''}`} 
                        className="w-full h-full object-none"
                        alt="North View"
                      />
                  </div>
                )}
              </div>
              <div className="flex-1 flex justify-center items-center min-h-0">
                {activeProject && (
                  <div 
                      className={`relative w-full h-full border overflow-hidden cursor-pointer transition-colors ${selectedElement === 'east' ? 'border-blue-500 border-2' : 'border-dashed border-slate-300'}`}
                      onClick={() => setSelectedElement('east')}
                  >
                      <div className="absolute top-2 left-2 bg-white px-2 text-xs font-bold font-mono text-slate-800 z-10 flex gap-2 items-center">
                        <span>SIDE ELEVATION (EAST)</span>
                        {customScales.east && <span className="text-slate-500 text-[10px]">SCALE {customScales.east}:1</span>}
                      </div>
                      <img 
                        src={`${serverUrl}/projects/${activeProject}/views/east.svg?t=${refreshKey}${getScale('east') ? `&scale=${getScale('east')}` : ''}`} 
                        className="w-full h-full object-none"
                        alt="East View"
                      />
                  </div>
                )}
              </div>
            </>
          )}

          {arrangement !== 'combined' && (
            <div className="flex-1 flex justify-center items-center min-h-0">
              {activeProject && (
                <div 
                    className={`relative w-full h-full border overflow-hidden cursor-pointer transition-colors ${selectedElement === arrangement ? 'border-blue-500 border-2' : 'border-dashed border-slate-300'}`}
                    onClick={() => setSelectedElement(arrangement as any)}
                >
                    <div className="absolute top-2 left-2 bg-white px-2 text-xs font-bold font-mono text-slate-800 z-10 flex gap-2 items-center">
                      <span>{arrangement === 'top' ? 'TOP VIEW' : arrangement === 'north' ? 'FRONT ELEVATION (NORTH)' : 'SIDE ELEVATION (EAST)'}</span>
                      {customScales[arrangement as 'top'|'north'|'east'] && <span className="text-slate-500 text-[10px]">SCALE {customScales[arrangement as 'top'|'north'|'east']}:1</span>}
                    </div>
                    <img 
                      src={`${serverUrl}/projects/${activeProject}/views/${arrangement}.svg?t=${refreshKey}${getScale(arrangement as 'top'|'north'|'east') ? `&scale=${getScale(arrangement as 'top'|'north'|'east')}` : ''}`} 
                      className="w-full h-full object-none"
                      alt={`${arrangement} View`}
                    />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Title Block */}
        <div 
          className={`bg-white border-t-2 border-l-2 border-black flex flex-col cursor-pointer transition-all z-20 ${selectedElement === 'title' ? 'ring-2 ring-blue-500 ring-offset-2' : ''}`} 
          style={{ position: 'absolute', bottom: '10mm', right: '10mm', width: '120mm' }}
          onClick={() => setSelectedElement('title')}
        >
          <div className="border-b border-black p-2 bg-slate-50 flex justify-between items-center">
            <span className="font-bold text-[10px] uppercase tracking-widest text-slate-500">Project</span>
            <span className="font-mono font-bold text-sm text-slate-900">{titleOverrides.project || activeProject || 'NO PROJECT'}</span>
          </div>
          <div className="flex border-b border-black">
            <div className="flex-1 border-r border-black p-2">
              <div className="text-[10px] text-slate-500 uppercase tracking-widest">Scale</div>
              <div className="font-mono font-bold text-sm text-slate-900">
                {getActiveScaleDisplay()}
              </div>
            </div>
            <div className="flex-1 p-2">
              <div className="text-[10px] text-slate-500 uppercase tracking-widest">Date</div>
              <div className="font-mono font-bold text-sm text-slate-900">{titleOverrides.date}</div>
            </div>
          </div>
          <div className="p-3 bg-white">
            <div className="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Drawing Title</div>
            <div className="font-bold text-lg font-serif text-slate-900 leading-tight">{titleOverrides.title}</div>
          </div>
          <div className="border-t border-black p-1 text-right text-[10px] bg-slate-900 text-white font-bold tracking-widest">
            TERTIUS // TIMUS
          </div>
        </div>

      </div>
    </div>
  );
};
