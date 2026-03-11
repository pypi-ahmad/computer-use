import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App.jsx'
import Workbench from './pages/Workbench.jsx'
import './index.css'

/**
 * Application entry point. Mounts the React root with BrowserRouter,
 * mapping '/' to the main App dashboard and '/workbench' to the Workbench page.
 */
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/workbench" element={<Workbench />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
