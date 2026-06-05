import React, { useState, useEffect } from 'react';

export const DraftingTab: React.FC<{ serverUrl: string }> = ({ serverUrl }) => {
  const intusUrl = serverUrl.replace('/timus', '/intus');
  const [activeProject, setActiveProject] = useState<string>('');
  const [refreshKey, setRefreshKey] = useState(0);
  
  // Customizer State
  const [title, setTitle] = useState('UNTITLED PART');
  const [stampText, setStampText] = useState('APPROVED');
  const [showRedline, setShowRedline] = useState(true);
  const [showHiddenLines, setShowHiddenLines] = useState(true);
  const [scale, setScale] = useState(1.0);
  const [sheetSize, setSheetSize] = useState('A4');
  
  // Debounced values for URL generation
  const [debouncedScale, setDebouncedScale] = useState(1.0);
  const [debouncedTitle, setDebouncedTitle] = useState('UNTITLED PART');
  
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedScale(scale), 300);
    return () => clearTimeout(timer);
  }, [scale]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedTitle(title), 500);
    return () => clearTimeout(timer);
  }, [title]);

  useEffect(() => {
    const fetchActive = async () => {
      try {
        const res = await fetch(`${intusUrl}/projects`);
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
    setTitle(activeProject.toUpperCase());
    
    // Load settings from local storage
    const saved = localStorage.getItem(`timus_settings_${activeProject}`);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (parsed.title) setTitle(parsed.title);
        if (parsed.stampText) setStampText(parsed.stampText);
        if (parsed.showRedline !== undefined) setShowRedline(parsed.showRedline);
        if (parsed.showHiddenLines !== undefined) setShowHiddenLines(parsed.showHiddenLines);
        if (parsed.scale) setScale(parsed.scale);
        if (parsed.sheetSize) setSheetSize(parsed.sheetSize);
      } catch (e) {}
    }
  }, [activeProject]);

  useEffect(() => {
    if (!activeProject) return;
    const settings = {
      title,
      stampText,
      showRedline,
      showHiddenLines,
      scale,
      sheetSize
    };
    localStorage.setItem(`timus_settings_${activeProject}`, JSON.stringify(settings));
  }, [activeProject, title, stampText, showRedline, showHiddenLines, scale, sheetSize]);

  useEffect(() => {
    if (!activeProject) return;
    let mtime = 0;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${intusUrl}/projects/${activeProject}/status`);
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

  const getPreviewUrl = () => {
    return `${serverUrl}/projects/${activeProject}/drafting.pdf?title=${encodeURIComponent(debouncedTitle)}&stamp=${encodeURIComponent(stampText)}&redline=${showRedline}&hidden_lines=${showHiddenLines}&scale=${debouncedScale}&size=${sheetSize}&t=${refreshKey}`;
  };

  useEffect(() => {
    if (!activeProject) return;
    let isMounted = true;
    
    const loadPdf = async () => {
      setIsGenerating(true);
      try {
        const res = await fetch(getPreviewUrl());
        if (res.ok && isMounted) {
          const blob = await res.blob();
          setPdfUrl(URL.createObjectURL(blob));
        }
      } catch (e) {
        console.error("Failed to load PDF preview:", e);
      }
      if (isMounted) setIsGenerating(false);
    };
    
    loadPdf();
    
    return () => { isMounted = false; };
  }, [activeProject, debouncedTitle, stampText, showRedline, showHiddenLines, debouncedScale, sheetSize, refreshKey]);

  if (!activeProject) {
    return (
      <div className="flex-1 flex justify-center items-center bg-slate-900 text-slate-500 font-mono text-sm">
        No active project found. Compile a project in Intus first.
      </div>
    );
  }

  return (
    <div className="flex-1 flex min-h-0 overflow-hidden bg-slate-900 selection:bg-cyan-500/30">
      {/* Customizer Controls Panel (Sidebar) */}
      <div className="w-80 border-r border-slate-800 bg-slate-950 p-6 flex flex-col justify-between overflow-y-auto">
        <div className="space-y-6">
          <div>
            <h3 className="text-lg font-bold text-orange-400">Timus Compiler</h3>
            <p className="text-xs text-slate-500">Automated A4 Drafting-Sheet PDF Generation.</p>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400">Sheet Size</label>
            <select
              value={sheetSize}
              onChange={(e) => setSheetSize(e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-orange-400"
            >
              <option value="A4">A4 (297 × 210 mm)</option>
              <option value="A3">A3 (420 × 297 mm)</option>
              <option value="A2">A2 (594 × 420 mm)</option>
              <option value="A1">A1 (841 × 594 mm)</option>
              <option value="A0">A0 (1189 × 841 mm)</option>
            </select>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400">Drawing Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-orange-400"
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400 flex justify-between">
              <span>Projection Scale</span>
              <span className="text-orange-400 font-bold">{scale.toFixed(3)}:1</span>
            </label>
            <input
              type="range"
              min="-3"
              max="0.69897"
              step="0.01"
              value={Math.log10(scale)}
              onChange={(e) => setScale(Math.pow(10, parseFloat(e.target.value)))}
              className="w-full h-1 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-orange-400"
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400">Redline Stamp Code</label>
            <input
              type="text"
              value={stampText}
              onChange={(e) => setStampText(e.target.value.substring(0, 16).toUpperCase())}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-orange-400"
              placeholder="e.g. APPROVED"
            />
            <div className="flex gap-1.5 flex-wrap">
              {['APPROVED', 'REJECTED', 'GAIN ENG', 'QTD OK'].map(preset => (
                <button
                  key={preset}
                  onClick={() => setStampText(preset)}
                  className="text-[9px] font-mono px-2 py-0.5 rounded bg-slate-900 hover:bg-slate-850 text-slate-500 hover:text-slate-300 border border-slate-800"
                >
                  {preset}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between border-t border-slate-900 pt-4">
            <span className="text-xs font-mono uppercase tracking-wider text-slate-400">Show Redline Markups</span>
            <button
              onClick={() => setShowRedline(!showRedline)}
              className={`relative inline-flex h-5 w-10 items-center rounded-full transition-colors outline-none ${
                showRedline ? 'bg-orange-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                  showRedline ? 'translate-x-5.5' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          <div className="flex items-center justify-between pt-1">
            <span className="text-xs font-mono uppercase tracking-wider text-slate-400">Show Hidden Lines</span>
            <button
              onClick={() => setShowHiddenLines(!showHiddenLines)}
              className={`relative inline-flex h-5 w-10 items-center rounded-full transition-colors outline-none ${
                showHiddenLines ? 'bg-orange-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                  showHiddenLines ? 'translate-x-5.5' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
        </div>

        <div className="space-y-2 pt-6 border-t border-slate-900">
          <button
            onClick={() => setRefreshKey(k => k + 1)}
            className="w-full bg-slate-900 hover:bg-slate-850 text-slate-300 hover:text-slate-100 font-semibold py-2 px-4 rounded border border-slate-800 transition-all text-xs flex justify-center items-center gap-1.5 cursor-pointer"
          >
            🔄 Refresh Compiler
          </button>
          <a
            href={getPreviewUrl()}
            target="_blank"
            rel="noreferrer"
            className="w-full bg-orange-600 hover:bg-orange-500 text-white font-bold py-2.5 px-4 rounded transition-all text-xs flex justify-center items-center gap-1.5 cursor-pointer shadow-lg shadow-orange-500/20"
          >
            📥 Download PDF
          </a>
        </div>
      </div>

      {/* Dynamic PDF Previews Area (Main Panel) */}
      <div className="flex-1 p-6 overflow-hidden flex flex-col items-center space-y-4 select-none bg-slate-800">
        <div className="text-center space-y-1">
          <h2 className="text-xl font-bold text-white tracking-wide">Timus PDF Compiler Preview</h2>
          <p className="text-xs text-slate-400">Live vector-sharp drafting sheet compiled by the backend.</p>
        </div>

        <div className="flex-1 w-full max-w-5xl bg-slate-950 border border-slate-800 shadow-2xl overflow-hidden rounded relative">
          {isGenerating && (
            <div className="absolute inset-0 z-10 bg-slate-900/80 backdrop-blur-sm flex flex-col items-center justify-center text-orange-400">
              <svg className="animate-spin h-10 w-10 mb-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <div className="font-bold tracking-widest text-sm animate-pulse">COMPILING VECTOR VIEWS...</div>
            </div>
          )}
          {pdfUrl && (
            <iframe
              src={`${pdfUrl}#toolbar=0&navpanes=0`}
              className="w-full h-full border-0 bg-white relative z-0"
              title="Timus Drafting Sheet Preview"
            />
          )}
        </div>
      </div>
    </div>
  );
};
