import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuth, useModal } from '../store'

export default function Navbar() {
  const { token, username, displayName, logout } = useAuth()
  const openModal = useModal(s => s.open)
  const navigate = useNavigate()
  const location = useLocation()

  function navToSection(id) {
    if (location.pathname !== '/') {
      navigate('/', { state: { scrollTo: id } })
    } else {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
    }
  }

  function goProtected(path) {
    if (!token) { openModal('login'); return }
    navigate(path)
  }

  return (
    <nav className="nav">
      <div className="nav-inner">
        <Link to="/" className="nav-logo">
          <div className="logo-icon">A</div>
          AlphaStock
        </Link>

        <ul className="nav-links">
          <li>
            <button className="nav-link-item" onClick={() => navToSection('intro')}>
              Project Intro
            </button>
          </li>
          <li className="dropdown">
            <span className="nav-link-item">
              Product
              <svg className="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </span>
            <div className="dropdown-menu">
              <button className="dropdown-item" onClick={() => goProtected('/backtest')}>
                <div className="dm-icon" style={{ background: '#ede9fe' }}>📊</div>
                <div>
                  <div className="dm-label">量化回测</div>
                  <div className="dm-desc">历史策略验证与收益分析</div>
                </div>
              </button>
              <button className="dropdown-item" onClick={() => goProtected('/stock?tab=alpha')}>
                <div className="dm-icon" style={{ background: '#fef9c3' }}>✨</div>
                <div>
                  <div className="dm-label">Alpha 选股</div>
                  <div className="dm-desc">多维评分筛选优质标的</div>
                </div>
              </button>
              <button className="dropdown-item" onClick={() => goProtected('/stock?tab=scan')}>
                <div className="dm-icon" style={{ background: '#dcfce7' }}>🎯</div>
                <div>
                  <div className="dm-label">今日买点</div>
                  <div className="dm-desc">全市场技术信号扫描</div>
                </div>
              </button>
              <button className="dropdown-item" onClick={() => goProtected('/stock?tab=filter')}>
                <div className="dm-icon" style={{ background: '#e0f2fe' }}>🔍</div>
                <div>
                  <div className="dm-label">板块筛选</div>
                  <div className="dm-desc">估值与景气度综合优选</div>
                </div>
              </button>
            </div>
          </li>
          <li>
            <button className="nav-link-item" onClick={() => goProtected('/chat')}>
              Alpha AI
            </button>
          </li>
          <li>
            <button className="nav-link-item" onClick={() => navToSection('contact')}>
              Contact
            </button>
          </li>
        </ul>

        <div className="nav-actions">
          {token ? (
            <>
              <span className="nav-username">👤 {displayName || username}</span>
              <button className="btn-start" onClick={() => goProtected('/chat')} style={{ padding: '10px 20px' }}>
                进入 AI
              </button>
              <button className="btn-logout" onClick={logout}>退出</button>
            </>
          ) : (
            <>
              <button className="btn-login" onClick={() => openModal('login')}>Log In</button>
              <button className="btn-start" onClick={() => goProtected('/chat')}>Get Started ›</button>
            </>
          )}
        </div>
      </div>
    </nav>
  )
}
