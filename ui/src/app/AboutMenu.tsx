import { useState } from 'react'

export function AboutMenu() {
  const [isOpen, setIsOpen] = useState(false)

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen((current) => !current)}
        className="px-3 py-2 mb-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors shrink-0"
        aria-expanded={isOpen}
      >
        About
      </button>
      {isOpen && (
        <div id="about-dropdown" className="absolute right-0 top-full mt-2 w-64 bg-slate-800 rounded-lg shadow-xl border border-slate-700 z-50">
          <div className="p-4 space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-slate-400">Frontend:</span>
              <span className="text-slate-200 font-mono">v1.0.0</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Backend:</span>
              <span className="text-slate-200 font-mono">v1.0.0</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Commit:</span>
              <span className="text-slate-200 font-mono">{__GIT_COMMIT__}</span>
            </div>
            <div className="pt-2 border-t border-slate-700">
              <a
                href="https://github.com/d-b-w-gain/Tertius-Web"
                target="_blank"
                rel="noopener noreferrer"
                className="text-cyan-400 hover:text-cyan-300 flex items-center justify-between"
              >
                <span>View on GitHub</span>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
