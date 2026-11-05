import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, HashRouter } from 'react-router-dom'
import App from './App'
import './index.css'
import { applyTheme, savedTheme } from './data/themes'

applyTheme(savedTheme())   // before the first paint, so there is no flash of the wrong colours

const Router = window.vault?.isElectron || window.location.protocol === 'file:' ? HashRouter : BrowserRouter

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Router>
      <App />
    </Router>
  </React.StrictMode>
)
