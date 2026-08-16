import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'

export default function ResetPassword() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get('token') || ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [status, setStatus] = useState('idle') // idle | loading | success | error
  const [msg, setMsg] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('error')
      setMsg('无效的重置链接')
    }
  }, [token])

  async function handleSubmit() {
    if (!password || password.length < 6) { setMsg('密码至少6位'); return }
    if (password !== confirm) { setMsg('两次密码不一致'); return }
    setStatus('loading')
    try {
      const d = await api.resetPassword(token, password)
      setStatus('success')
      setMsg(d.message || '密码已重置')
    } catch (e) {
      setStatus('error')
      setMsg(e.message || '重置失败，链接可能已过期')
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: 'var(--bg)',
    }}>
      <div style={{
        width: 380, padding: '40px 36px', borderRadius: 16,
        background: 'var(--surface)', boxShadow: '0 8px 40px rgba(0,0,0,0.15)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 28 }}>
          <div style={{
            width: 48, height: 48, borderRadius: 12,
            background: 'linear-gradient(135deg,#4f46e5,#7c3aed)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 22, fontWeight: 700, color: '#fff', margin: '0 auto 12px',
          }}>A</div>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>重置密码</h2>
          <p style={{ margin: '6px 0 0', color: 'var(--text-2)', fontSize: 13 }}>
            AlphaStock · 设置新密码
          </p>
        </div>

        {status === 'success' ? (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>✅</div>
            <p style={{ color: 'var(--text-1)', marginBottom: 20 }}>{msg}</p>
            <button className="m-submit" onClick={() => navigate('/')}>
              返回首页登录
            </button>
          </div>
        ) : (
          <>
            <div className="m-group">
              <label className="m-label">新密码</label>
              <input
                className="m-input"
                type="password"
                placeholder="至少6位"
                value={password}
                onChange={e => { setPassword(e.target.value); setMsg('') }}
              />
            </div>
            <div className="m-group">
              <label className="m-label">确认密码</label>
              <input
                className="m-input"
                type="password"
                placeholder="再次输入新密码"
                value={confirm}
                onChange={e => { setConfirm(e.target.value); setMsg('') }}
                onKeyDown={e => e.key === 'Enter' && handleSubmit()}
              />
            </div>
            {msg && (
              <div className="m-error">{msg}</div>
            )}
            <button
              className="m-submit"
              onClick={handleSubmit}
              disabled={status === 'loading' || !token}
            >
              {status === 'loading' ? '重置中…' : '确认重置'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
