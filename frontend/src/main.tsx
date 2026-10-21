import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
// local font files (the demo runs without network, and filtered networks block font CDNs)
import '@fontsource/heebo/hebrew-200.css'
import '@fontsource/heebo/hebrew-300.css'
import '@fontsource/heebo/hebrew-400.css'
import '@fontsource/heebo/hebrew-500.css'
import '@fontsource/heebo/hebrew-700.css'
import '@fontsource/heebo/latin-200.css'
import '@fontsource/heebo/latin-300.css'
import '@fontsource/heebo/latin-400.css'
import '@fontsource/heebo/latin-500.css'
import '@fontsource/heebo/latin-700.css'
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
