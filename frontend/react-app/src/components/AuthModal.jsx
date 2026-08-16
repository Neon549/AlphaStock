import { useState, useEffect, useRef } from 'react'
import { useAuth, useModal } from '../store'
import { api } from '../api'
import { useGoogleLogin } from '@react-oauth/google'

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
    </svg>
  )
}

function WechatIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="#07c160">
      <path d="M8.5 3C4.36 3 1 6.13 1 10c0 2.17 1.06 4.08 2.72 5.37L3 18l2.87-1.44C7.04 16.83 8.24 17 9.5 17h.08A6.5 6.5 0 009 14.5C9 10.91 12.36 8 16.5 8c.17 0 .34 0 .5.01C16.04 5.15 12.57 3 8.5 3zM6 8.5a1 1 0 110 2 1 1 0 010-2zm5 0a1 1 0 110 2 1 1 0 010-2z"/>
      <path d="M16.5 9C13.47 9 11 11.24 11 14s2.47 5 5.5 5c.92 0 1.78-.22 2.54-.6L21 19.5l-.62-2.38A4.92 4.92 0 0022 14c0-2.76-2.47-5-5.5-5zm-2 4a.75.75 0 110 1.5.75.75 0 010-1.5zm4 0a.75.75 0 110 1.5.75.75 0 010-1.5z"/>
    </svg>
  )
}

function QQIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="#1d7de3">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.5 0 2.88.55 3.94 1.44C14.97 7.61 14 9.7 14 12c0 .7.1 1.38.28 2.02-.74.28-1.48.48-2.28.48s-1.54-.2-2.28-.48C9.9 13.38 10 12.7 10 12c0-2.3-.97-4.39-2.94-5.56A7.01 7.01 0 0112 5zm0 14c-3.87 0-7-3.13-7-7 0-.85.16-1.66.44-2.41.83.87 2.14 1.41 3.56 1.41.7 0 1.37-.15 1.98-.4.28.9.44 1.87.44 2.87 0 1.16-.22 2.26-.62 3.26-.6.18-1.21.27-1.8.27-.97 0-1.88-.27-2.67-.73.68 1.52 2.21 2.73 4.07 2.73s3.39-1.21 4.07-2.73c-.79.46-1.7.73-2.67.73-.59 0-1.2-.09-1.8-.27A8.2 8.2 0 0013 13.92c0-1 .16-1.97.44-2.87.61.25 1.28.4 1.98.4 1.42 0 2.73-.54 3.56-1.41.28.75.44 1.56.44 2.41 0 3.87-3.13 7-7 7z"/>
    </svg>
  )
}

export default function AuthModal() {
  const { isOpen, tab, close, open } = useModal()
  const { login } = useAuth()
  const [loginUser, setLoginUser] = useState('')
  const [loginPass, setLoginPass] = useState('')
  const [regUser, setRegUser] = useState('')
  const [regPass, setRegPass] = useState('')
  const [regEmail, setRegEmail] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [pwChecks, setPwChecks] = useState({ len: false, lower: false, upper: false, num: false })
  const [forgotMode, setForgotMode] = useState(false)
  const [forgotEmail, setForgotEmail] = useState('')
  const [forgotSent, setForgotSent] = useState(false)
  const [googleLoading, setGoogleLoading] = useState(false)
  const overlayRef = useRef()
  const googleWatchdogRef = useRef(null)

  function clearGoogleWatchdog() {
    if (googleWatchdogRef.current) {
      clearTimeout(googleWatchdogRef.current)
      googleWatchdogRef.current = null
    }
  }

  useEffect(() => {
    if (!isOpen) {
      setError('')
      setLoginUser(''); setLoginPass('')
      setRegUser(''); setRegPass(''); setRegEmail('')
      setForgotMode(false); setForgotEmail(''); setForgotSent(false)
      setGoogleLoading(false)
      clearGoogleWatchdog()
    }
  }, [isOpen])

  useEffect(() => () => clearGoogleWatchdog(), [])

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  function checkPw(p) {
    setPwChecks({
      len: p.length >= 8,
      lower: /[a-z]/.test(p),
      upper: /[A-Z]/.test(p),
      num: /\d/.test(p),
    })
  }

  async function doLogin() {
    setError('')
    if (!loginUser || !loginPass) { setError('请输入用户名和密码'); return }
    setLoading(true)
    try {
      const d = await api.login(loginUser, loginPass)
      login(d.token, d.username)
      close()
    } catch (e) {
      const msg = e.message || ''
      setError(msg === 'Failed to fetch' || msg === 'fetch failed' ? '服务暂时不可用，请稍后重试' : msg || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  async function doRegister() {
    setError('')
    if (!regUser || !regPass) { setError('请填写完整信息'); return }
    if (regPass.length < 8) { setError('密码至少8位'); return }
    setLoading(true)
    try {
      const d = await api.register(regUser, regPass, regEmail)
      login(d.token, d.username)
      close()
    } catch (e) {
      const msg = e.message || ''
      setError(msg === 'Failed to fetch' || msg === 'fetch failed' ? '服务暂时不可用，请稍后重试' : msg || '注册失败')
    } finally {
      setLoading(false)
    }
  }

  async function doForgotPassword() {
    setError('')
    if (!forgotEmail) { setError('请输入邮箱'); return }
    setLoading(true)
    try {
      await api.forgotPassword(forgotEmail)
      setForgotSent(true)
    } catch (e) {
      setError(e.message || '发送失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  const googleLogin = useGoogleLogin({
    onSuccess: async (tokenResponse) => {
      clearGoogleWatchdog()
      if (!tokenResponse?.access_token) {
        setGoogleLoading(false)
        setError('Google 未返回有效授权令牌，请重试')
        return
      }
      try {
        setGoogleLoading(true)
        // The backend validates the Google-issued bearer token and derives
        // identity from Google, rather than trusting browser-supplied profile fields.
        const d = await api.googleToken(tokenResponse.access_token)
        login(d.token, d.username)
        close()
      } catch (e) {
        const msg = e.message || ''
        setError(msg === 'Failed to fetch' || msg === 'fetch failed'
          ? '服务暂时不可用，请稍后重试'
          : msg || 'Google 登录失败，请稍后重试')
      } finally {
        setGoogleLoading(false)
      }
    },
    onError: () => {
      clearGoogleWatchdog()
      setGoogleLoading(false)
      setError('Google 登录被取消')
    },
  })

  function handleGoogleLogin() {
    if (loading || googleLoading) return
    setError('')
    setGoogleLoading(true)
    clearGoogleWatchdog()
    googleWatchdogRef.current = setTimeout(() => {
      setGoogleLoading(false)
      setError('Google 登录等待超时，请检查弹窗是否被浏览器拦截后重试')
    }, 30000)
    googleLogin()
  }

  function handleWechatLogin() {
    alert('微信网页登录需要在微信开放平台完成开发者资质、网站应用和登录能力审核，并配置 AppID、AppSecret、授权回调域名后才能启用。当前尚未配置。')
  }

  function handleQQLogin() {
    alert('QQ 登录需要在 QQ 互联/腾讯开放平台创建并审核网站应用，配置 AppID、AppKey、回调地址和备案域名后才能启用。当前尚未配置。')
  }

  return (
    <div
      ref={overlayRef}
      className={`modal-overlay${isOpen ? ' open' : ''}`}
      onClick={e => { if (e.target === overlayRef.current) close() }}
    >
      <div className="modal">
        <button className="modal-close" onClick={close}>✕</button>

        <div className="modal-logo">
          <div className="m-icon">A</div>
          <div className="modal-title">Welcome to AlphaStock</div>
          <div className="modal-sub">智能投研助手 · 登录后开始使用</div>
        </div>

        <div className="m-tabs">
          <button className={`m-tab${tab === 'login' ? ' active' : ''}`} onClick={() => { open('login'); setError('') }}>
            安全登录
          </button>
          <button className={`m-tab${tab === 'register' ? ' active' : ''}`} onClick={() => { open('register'); setError('') }}>
            注册账号
          </button>
        </div>

        {tab === 'login' ? (
          <div className="m-panel active">
            {forgotMode ? (
              forgotSent ? (
                <div style={{ textAlign: 'center', padding: '16px 0' }}>
                  <div style={{ fontSize: 36, marginBottom: 12 }}>📬</div>
                  <p style={{ color: 'var(--text-1)', marginBottom: 8 }}>重置邮件已发送！</p>
                  <p style={{ color: 'var(--text-2)', fontSize: 13 }}>请查收 {forgotEmail} 的邮件，点击链接重置密码。链接1小时内有效。</p>
                  <button className="m-link-btn" onClick={() => { setForgotMode(false); setForgotSent(false); setForgotEmail('') }}>
                    返回登录
                  </button>
                </div>
              ) : (
                <>
                  <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
                    输入注册时使用的邮箱，我们将发送重置链接。
                  </p>
                  <div className="m-group">
                    <label className="m-label">注册邮箱</label>
                    <input className="m-input" type="email" placeholder="your@email.com"
                      value={forgotEmail} onChange={e => { setForgotEmail(e.target.value); setError('') }}
                      onKeyDown={e => e.key === 'Enter' && doForgotPassword()} autoFocus />
                  </div>
                  {error && <div className="m-error">{error}</div>}
                  <button className="m-submit" onClick={doForgotPassword} disabled={loading}>
                    {loading ? '发送中…' : '发送重置邮件'}
                  </button>
                  <button className="m-link-btn" onClick={() => { setForgotMode(false); setError('') }}>
                    ← 返回登录
                  </button>
                </>
              )
            ) : (
              <>
                <div className="m-group">
                  <label className="m-label">用户名</label>
                  <input className="m-input" placeholder="输入用户名" value={loginUser}
                    onChange={e => setLoginUser(e.target.value)} autoComplete="off" />
                </div>
                <div className="m-group">
                  <label className="m-label" style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span>密码</span>
                    <button className="m-link-btn" style={{ fontSize: 12 }}
                      onClick={() => { setForgotMode(true); setError('') }}>
                      忘记密码？
                    </button>
                  </label>
                  <input className="m-input" type="password" placeholder="输入密码" value={loginPass}
                    onChange={e => setLoginPass(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && doLogin()} />
                </div>
                {error && <div className="m-error">{error}</div>}
                <button className="m-submit" onClick={doLogin} disabled={loading}>
                  {loading ? '登录中…' : '登录'}
                </button>
                <div className="m-divider"><span>或使用第三方账号登录</span></div>
                <div className="m-socials">
                  <button className="m-social-btn" onClick={handleGoogleLogin} disabled={loading || googleLoading}>
                    <GoogleIcon /> {googleLoading ? 'Google 登录中…' : 'Google 登录'}
                  </button>
                  <div className="m-socials-row">
                    <button className="m-social-btn m-social-half" onClick={handleWechatLogin}>
                      <WechatIcon /> 微信登录
                    </button>
                    <button className="m-social-btn m-social-half" onClick={handleQQLogin}>
                      <QQIcon /> QQ 登录
                    </button>
                  </div>
                  <div className="m-social-note">微信 / QQ 登录待完成平台应用审核；账号密码和 Google 登录不受影响。</div>
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="m-panel active">
            <div className="m-group">
              <label className="m-label">用户名</label>
              <input className="m-input" placeholder="设置用户名（4-20位）" value={regUser}
                onChange={e => setRegUser(e.target.value)} autoComplete="off" />
            </div>
            <div className="m-group">
              <label className="m-label">邮箱 <span style={{ color: 'var(--text-2)', fontWeight: 400 }}>(用于找回密码)</span></label>
              <input className="m-input" type="email" placeholder="your@email.com（推荐填写）"
                value={regEmail} onChange={e => setRegEmail(e.target.value)} />
            </div>
            <div className="m-group">
              <label className="m-label">密码</label>
              <input className="m-input" type="password" placeholder="至少8位，含大小写和数字"
                value={regPass} onChange={e => { setRegPass(e.target.value); checkPw(e.target.value) }} />
              {regPass && (
                <div className="pw-grid">
                  <div className={`pw-item${pwChecks.len ? ' ok' : ''}`}>{pwChecks.len ? '✓' : '○'} 8位以上</div>
                  <div className={`pw-item${pwChecks.lower ? ' ok' : ''}`}>{pwChecks.lower ? '✓' : '○'} 包含小写</div>
                  <div className={`pw-item${pwChecks.upper ? ' ok' : ''}`}>{pwChecks.upper ? '✓' : '○'} 包含大写</div>
                  <div className={`pw-item${pwChecks.num ? ' ok' : ''}`}>{pwChecks.num ? '✓' : '○'} 包含数字</div>
                </div>
              )}
            </div>
            {error && <div className="m-error">{error}</div>}
            <button className="m-submit" onClick={doRegister} disabled={loading}>
              {loading ? '注册中…' : '注册并进入'}
            </button>
            <div className="m-divider"><span>或使用第三方账号注册</span></div>
            <div className="m-socials">
              <button className="m-social-btn" onClick={handleGoogleLogin} disabled={loading || googleLoading}>
                <GoogleIcon /> {googleLoading ? 'Google 登录中…' : 'Google 账号注册/登录'}
              </button>
              <div className="m-socials-row">
                <button className="m-social-btn m-social-half" onClick={handleWechatLogin}>
                  <WechatIcon /> 微信
                </button>
                <button className="m-social-btn m-social-half" onClick={handleQQLogin}>
                  <QQIcon /> QQ
                </button>
              </div>
              <div className="m-social-note">微信 / QQ 登录待完成平台应用审核；账号密码和 Google 登录不受影响。</div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
