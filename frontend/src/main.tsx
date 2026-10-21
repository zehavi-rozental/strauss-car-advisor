import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
// local font files (the demo runs without network, and filtered networks block font CDNs)
import '@fontsource/assistant/hebrew-300.css'
import '@fontsource/assistant/hebrew-400.css'
import '@fontsource/assistant/hebrew-600.css'
import '@fontsource/assistant/hebrew-700.css'
import '@fontsource/assistant/hebrew-800.css'
import '@fontsource/assistant/latin-300.css'
import '@fontsource/assistant/latin-400.css'
import '@fontsource/assistant/latin-600.css'
import '@fontsource/assistant/latin-700.css'
import '@fontsource/assistant/latin-800.css'
import './styles.css'
import App from './App.tsx'
import { applyMotion } from './art/motion'

applyMotion()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
