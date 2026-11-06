import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { initializeTelemetry } from './telemetry'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthProvider'
import { SharedProjectPage } from './share/SharedProjectPage'
import { isSharePage, takeShareToken } from './share/shareToken'

// A share link's token must be out of the address bar before telemetry
// records the page URL.
const sharePage = isSharePage()
const shareToken = sharePage ? takeShareToken() : null

initializeTelemetry()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      {sharePage ? <SharedProjectPage token={shareToken} /> : <App />}
    </AuthProvider>
  </StrictMode>,
)
