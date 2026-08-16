import { Routes, Route, useLocation } from 'react-router-dom'
import Navbar from './components/Navbar'
import AuthModal from './components/AuthModal'
import Home from './pages/Home'
import Chat from './pages/Chat'
import Backtest from './pages/Backtest'
import Stock from './pages/Stock'
import ResetPassword from './pages/ResetPassword'
import Governance from './pages/Governance'

export default function App() {
  const location = useLocation()
  const isLandingPage = location.pathname === '/'

  return (
    <>
      {isLandingPage && <Navbar />}
      <AuthModal />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/backtest" element={<Backtest />} />
        <Route path="/stock" element={<Stock />} />
        <Route path="/governance" element={<Governance />} />
        <Route path="/reset-password" element={<ResetPassword />} />
      </Routes>
    </>
  )
}
