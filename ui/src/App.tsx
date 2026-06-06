import { useState } from 'react'
import { IntusWindow } from './workflows/intus/IntusWindow'
import { ExtusWindow } from './workflows/extus/ExtusWindow'
import { ArtusWindow } from './workflows/artus/ArtusWindow'
import { TimusWindow } from './workflows/timus/TimusWindow'

function App() {
  const [activeTab, setActiveTab] = useState('extus')
  const [isSidebarOpen, setIsSidebarOpen] = useState(window.innerWidth >= 768)

  return (
    <div className="flex h-screen w-screen bg-slate-950 text-slate-100 overflow-hidden font-sans">
      {/* Mobile Backdrop */}
      {isSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-10 md:hidden transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      {/* Permanent Artus Feature Tree (Sidebar) */}
      <div 
        className={`absolute z-20 h-full md:relative md:h-auto border-r border-slate-800 bg-slate-900/95 md:bg-slate-900/50 flex flex-col transition-all duration-300 ease-in-out overflow-hidden ${
          isSidebarOpen ? 'w-96 translate-x-0' : 'w-96 -translate-x-full md:w-0 md:translate-x-0 md:border-r-0'
        }`}
      >
        <div className="p-4 border-b border-slate-800 flex items-center justify-between min-w-[20rem]">
          <div>
            <h1 className="text-xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent">
              Tertius
            </h1>
            <p className="text-xs text-slate-500 mt-1">Open Source CAD Toolkit</p>
          </div>
          <button 
            className="md:hidden text-slate-400 hover:text-white"
            onClick={() => setIsSidebarOpen(false)}
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        
        <div className="flex-1 flex flex-col min-h-0 min-w-[20rem]">
          <ArtusWindow />
        </div>
      </div>

      {/* Main Workflow Viewport (Tabbed) */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* Tab Header */}
        <div className="flex bg-slate-900 border-b border-slate-800 px-4 pt-4 gap-2 overflow-x-auto whitespace-nowrap scrollbar-hide">
          <button 
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            className="p-2 mb-2 mr-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors shrink-0 flex items-center justify-center"
            title="Toggle Sidebar"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
          
          <button 
            onClick={() => setActiveTab('extus')}
            className={`px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${activeTab === 'extus' ? 'bg-slate-950 text-cyan-300 font-medium border-slate-800' : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`}
          >
            👁️ Extus Viewport
          </button>
          <button 
            onClick={() => setActiveTab('intus')}
            className={`px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${activeTab === 'intus' ? 'bg-slate-950 text-indigo-300 font-medium border-slate-800' : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`}
          >
            ⚙️ Intus Compiler
          </button>
          <button 
            onClick={() => setActiveTab('timus')}
            className={`px-4 py-2 rounded-t-lg transition-all border-t border-l border-r ${activeTab === 'timus' ? 'bg-slate-950 text-emerald-300 font-medium border-slate-800' : 'bg-slate-800/50 hover:bg-slate-800 text-slate-400 border-transparent'}`}
          >
            📐 Timus Drafting
          </button>
        </div>

        <div className="flex-1 relative flex flex-col min-h-0 bg-slate-950">
          <div className={activeTab === 'extus' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
            <ExtusWindow isActive={activeTab === 'extus'} />
          </div>
          <div className={activeTab === 'intus' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
            <IntusWindow isActive={activeTab === 'intus'} />
          </div>
          <div className={activeTab === 'timus' ? 'absolute inset-0 flex flex-col' : 'hidden'}>
            <TimusWindow isActive={activeTab === 'timus'} />
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
