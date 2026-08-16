import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { GoogleOAuthProvider } from '@react-oauth/google'
import App from './App'
import './index.css'

// The production Nginx location is /app, while local development serves /
// directly. Detect it once so both entry points resolve the same routes.
const basename = window.location.pathname === '/app' || window.location.pathname.startsWith('/app/') ? '/app' : '/'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <GoogleOAuthProvider clientId="322349106189-a3tjujsd4guheppbl2uh8cv5nnbgbdu0.apps.googleusercontent.com">
      <BrowserRouter basename={basename}>
        <App />
      </BrowserRouter>
    </GoogleOAuthProvider>
  </React.StrictMode>
)
