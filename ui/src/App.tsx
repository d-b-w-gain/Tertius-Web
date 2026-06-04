import { useState } from 'react'
import { IntusWindow } from './workflows/intus/IntusWindow'
import { ExtusWindow } from './workflows/extus/ExtusWindow'
import { ArtusWindow } from './workflows/artus/ArtusWindow'
import { TimusWindow } from './workflows/timus/TimusWindow'

function App() {
  const [activeWorkflow, setActiveWorkflow] = useState('intus')

  return (
    <div className="flex h-screen w-screen bg-slate-950 text-slate-100 overflow-hidden font-sans">
      {/* Sidebar Navigation */}
      <div className="w-64 border-r border-slate-800 bg-slate-900/50 flex flex-col">
        <div className="p-6 border-b border-slate-800">
          <h1 className="text-xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent">
            Tertius
          </h1>
          <p className="text-xs text-slate-500 mt-1">Open Source CAD Toolkit</p>
        </div>
        
        <div className="flex-1 p-4 flex flex-col gap-2">
          <button 
            onClick={() => setActiveWorkflow('intus')}
            className={`text-left px-4 py-3 rounded-lg transition-all ${activeWorkflow === 'intus' ? 'bg-indigo-500/20 text-indigo-300 font-medium border border-indigo-500/30' : 'hover:bg-slate-800 text-slate-400 border border-transparent'}`}
          >
            ⚙️ Intus Compiler
          </button>
          
          <button 
            onClick={() => setActiveWorkflow('extus')}
            className={`text-left px-4 py-3 rounded-lg transition-all ${activeWorkflow === 'extus' ? 'bg-cyan-500/20 text-cyan-300 font-medium border border-cyan-500/30' : 'hover:bg-slate-800 text-slate-400 border border-transparent'}`}
          >
            👁️ Extus Viewer
          </button>

          <button 
            onClick={() => setActiveWorkflow('artus')}
            className={`text-left px-4 py-3 rounded-lg transition-all ${activeWorkflow === 'artus' ? 'bg-purple-500/20 text-purple-300 font-medium border border-purple-500/30' : 'hover:bg-slate-800 text-slate-400 border border-transparent'}`}
          >
            🌳 Artus Feature Tree
          </button>

          <button 
            onClick={() => setActiveWorkflow('timus')}
            className={`text-left px-4 py-3 rounded-lg transition-all ${activeWorkflow === 'timus' ? 'bg-emerald-500/20 text-emerald-300 font-medium border border-emerald-500/30' : 'hover:bg-slate-800 text-slate-400 border border-transparent'}`}
          >
            📐 Timus Drafting
          </button>
        </div>
      </div>

      {/* Main Workflow Viewport */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {activeWorkflow === 'intus' && <IntusWindow />}
        {activeWorkflow === 'extus' && <ExtusWindow />}
        {activeWorkflow === 'artus' && <ArtusWindow />}
        {activeWorkflow === 'timus' && <TimusWindow />}
      </div>
    </div>
  )
}

export default App
