import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, useModal } from '../store'
import { api } from '../api'

const MODES = [
  {
    key: 'safe',
    icon: '🛡️',
    label: '安全模式',
    description: '所有长期记忆候选保留为待审核，不自动写入知识库。',
    note: '适合默认使用',
    color: '#2563eb',
  },
  {
    key: 'assist',
    icon: '✨',
    label: '帮我审批',
    description: '低风险经验自动处理，中高风险候选合并为一次确认。',
    note: '减少重复确认',
    color: '#7c3aed',
  },
  {
    key: 'full_access',
    icon: '⚠️',
    label: '完全访问权限',
    description: '短时提升自动化范围，但硬阻断和高风险操作仍不可绕过。',
    note: '需要风险确认',
    color: '#ea580c',
  },
]

const RISK_LABELS = { low: '低风险', medium: '中风险', high: '高风险' }

function AuthGate() {
  const open = useModal(s => s.open)
  return (
    <div className="gov-auth-gate">
      <div className="gov-empty-icon">🔐</div>
      <h1>权限与审核</h1>
      <p>登录后才能管理 Agent 的审核模式和长期记忆候选。</p>
      <button className="btn-start" onClick={() => open('login')}>登录后继续</button>
    </div>
  )
}

function FullAccessDialog({ onConfirm, onCancel, loading }) {
  const [ttl, setTtl] = useState(30)
  const [confirmed, setConfirmed] = useState(false)
  return (
    <div className="gov-dialog-backdrop" role="presentation" onMouseDown={e => e.target === e.currentTarget && onCancel()}>
      <div className="gov-dialog" role="dialog" aria-modal="true" aria-labelledby="full-access-title">
        <div className="gov-dialog-icon">⚠️</div>
        <h2 id="full-access-title">启用完全访问权限？</h2>
        <p>这是一次短时授权，仅影响审核自动化范围，不会开放交易下单、发布决策或绕过后端硬阻断。</p>
        <div className="gov-risk-list">
          <div>✓ 低/中风险长期记忆可按策略自动处理</div>
          <div>✓ 实时行情、当前财务事实、投资推荐不会进入长期记忆</div>
          <div>✓ 高风险候选仍需要人工确认，并保留审计记录</div>
        </div>
        <label className="gov-field-label" htmlFor="access-ttl">授权时长</label>
        <select id="access-ttl" className="gov-select" value={ttl} onChange={e => setTtl(Number(e.target.value))}>
          {[15, 30, 60, 120].map(value => <option key={value} value={value}>{value} 分钟</option>)}
        </select>
        <label className="gov-check-row">
          <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
          我已了解风险，并确认启用完全访问权限
        </label>
        <div className="gov-dialog-actions">
          <button className="gov-secondary-btn" onClick={onCancel}>取消</button>
          <button className="gov-primary-btn" disabled={!confirmed || loading} onClick={() => onConfirm(ttl)}>
            {loading ? '启用中…' : '确认启用'}
          </button>
        </div>
      </div>
    </div>
  )
}

function BatchDialog({ count, onConfirm, onCancel, loading }) {
  const [approved, setApproved] = useState(true)
  const [note, setNote] = useState('')
  return (
    <div className="gov-dialog-backdrop" role="presentation" onMouseDown={e => e.target === e.currentTarget && onCancel()}>
      <div className="gov-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-title">
        <h2 id="batch-title">确认批量审核</h2>
        <p>你选择了 {count} 条候选。一次确认会统一记录审核人、动作和意见。</p>
        <div className="gov-choice-row">
          <button className={approved ? 'selected' : ''} onClick={() => setApproved(true)}>✅ 批准所选</button>
          <button className={!approved ? 'selected danger' : ''} onClick={() => setApproved(false)}>拒绝所选</button>
        </div>
        <textarea className="gov-note" value={note} onChange={e => setNote(e.target.value)} placeholder="审核意见（可选）" rows={3} />
        <div className="gov-dialog-actions">
          <button className="gov-secondary-btn" onClick={onCancel}>取消</button>
          <button className={`gov-primary-btn${approved ? '' : ' danger-btn'}`} disabled={loading} onClick={() => onConfirm(approved, note)}>
            {loading ? '提交中…' : approved ? '确认批准' : '确认拒绝'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Governance() {
  const { token, username } = useAuth()
  const navigate = useNavigate()
  const [modeState, setModeState] = useState(null)
  const [candidates, setCandidates] = useState([])
  const [selected, setSelected] = useState([])
  const [dialog, setDialog] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    if (!token) return
    setError('')
    try {
      const [mode, candidatePayload] = await Promise.all([
        api.getApprovalMode(),
        api.getMemoryCandidates('pending'),
      ])
      setModeState(mode)
      setCandidates(candidatePayload.candidates || [])
    } catch (err) {
      setError(err.message || '无法读取权限状态')
    }
  }

  useEffect(() => { load() }, [token])

  const currentMode = modeState?.mode || 'safe'
  const currentMeta = MODES.find(mode => mode.key === currentMode) || MODES[0]
  const counts = useMemo(() => candidates.reduce((acc, item) => {
    acc[item.risk_level || 'high'] = (acc[item.risk_level || 'high'] || 0) + 1
    return acc
  }, {}), [candidates])

  async function changeMode(nextMode, ttl) {
    setLoading(true)
    setError('')
    try {
      const next = await api.setApprovalMode(nextMode === 'full_access'
        ? { mode: nextMode, confirm_risk: true, ttl_minutes: ttl }
        : { mode: nextMode })
      setModeState(next)
      setDialog(null)
      setNotice(`已切换到${MODES.find(mode => mode.key === nextMode)?.label}。`)
    } catch (err) {
      setError(err.message || '权限模式切换失败')
    } finally {
      setLoading(false)
    }
  }

  async function reviewSelected(approved, reviewNote) {
    setLoading(true)
    setError('')
    try {
      const result = await api.batchMemoryDecision(selected, approved, reviewNote)
      setCandidates(prev => prev.filter(item => !selected.includes(item.candidate_id)))
      setSelected([])
      setDialog(null)
      setNotice(`已${approved ? '批准' : '拒绝'} ${result.count || selected.length} 条候选。${approved ? '批准后的 Markdown 仍需同步 pgvector。' : ''}`)
    } catch (err) {
      setError(err.message || '批量审核失败')
    } finally {
      setLoading(false)
    }
  }

  if (!token) return <AuthGate />

  return (
    <div className="gov-page">
      <header className="gov-header">
        <div>
          <div className="gov-eyebrow">GOVERNANCE CENTER</div>
          <h1>权限与审核</h1>
          <p>一次选择审核模式，系统按三层风险漏斗处理 Agent 的长期记忆与高影响操作。</p>
        </div>
        <div className="gov-header-actions">
          <span className="gov-user">👤 {username}</span>
          <button className="gov-secondary-btn" onClick={() => navigate('/chat')}>返回 coding agent</button>
        </div>
      </header>

      {notice && <div className="gov-notice">✓ {notice}<button onClick={() => setNotice('')}>×</button></div>}
      {error && <div className="gov-error">{error}</div>}

      <section className="gov-current-bar">
        <div><span className="gov-status-dot" style={{ background: currentMeta.color }} />当前模式：<strong>{currentMeta.label}</strong></div>
        {modeState?.elevated_expires_at && <span>授权截止：{modeState.elevated_expires_at}</span>}
      </section>

      <section>
        <div className="gov-section-heading"><h2>选择审核模式</h2><span>模式只改变确认频率，不会关闭硬阻断</span></div>
        <div className="gov-mode-grid">
          {MODES.map(mode => (
            <article key={mode.key} className={`gov-mode-card${currentMode === mode.key ? ' active' : ''}`} style={{ '--mode-color': mode.color }}>
              <div className="gov-mode-icon">{mode.icon}</div>
              <div className="gov-mode-title">{mode.label}{currentMode === mode.key && <span>当前</span>}</div>
              <p>{mode.description}</p>
              <small>{mode.note}</small>
              <button
                className={currentMode === mode.key ? 'gov-mode-btn disabled' : 'gov-mode-btn'}
                disabled={currentMode === mode.key}
                onClick={() => mode.key === 'full_access' ? setDialog({ type: 'full' }) : changeMode(mode.key)}
              >
                {currentMode === mode.key ? '已启用' : mode.key === 'full_access' ? '查看风险并启用' : `切换到${mode.label}`}
              </button>
            </article>
          ))}
        </div>
      </section>

      <section>
        <div className="gov-section-heading"><h2>三层权限漏斗</h2><span>对用户可见、可审计、可回退</span></div>
        <div className="gov-funnel">
          <div><b className="blocked">1 · 硬阻断</b><p>实时行情、当前财务事实、投资推荐、密钥和隐私信息不能进入长期记忆。</p></div>
          <div><b className="risk">2 · 风险分级</b><p>低风险可自动处理，中高风险聚合成一次批量确认。</p></div>
          <div><b className="confirm">3 · 显式确认</b><p>完全访问权限有时效，高风险发布与交易永远不能绕过人工审核。</p></div>
        </div>
      </section>

      <section className="gov-candidates">
        <div className="gov-section-heading">
          <div><h2>待审核长期记忆</h2><span>{candidates.length} 条候选 · 低风险 {counts.low || 0} · 中风险 {counts.medium || 0} · 高风险 {counts.high || 0}</span></div>
          {selected.length > 0 && <div className="gov-batch-actions"><button className="gov-secondary-btn" onClick={() => setDialog({ type: 'batch' })}>批量审核 {selected.length} 条</button><button className="gov-link-btn" onClick={() => setSelected([])}>清空选择</button></div>}
        </div>
        {candidates.length === 0 ? <div className="gov-empty">当前没有待审核候选。新的经验会根据当前模式自动分流。</div> : (
          <div className="gov-candidate-list">
            {candidates.map(candidate => {
              const risk = candidate.risk_level || 'high'
              return (
                <label className="gov-candidate" key={candidate.candidate_id}>
                  <input type="checkbox" checked={selected.includes(candidate.candidate_id)} onChange={e => setSelected(prev => e.target.checked ? [...prev, candidate.candidate_id] : prev.filter(id => id !== candidate.candidate_id))} />
                  <div className="gov-candidate-body">
                    <div className="gov-candidate-head"><strong>{candidate.title || '未命名经验'}</strong><span className={`risk-pill ${risk}`}>{RISK_LABELS[risk] || '需确认'}</span></div>
                    <div className="gov-candidate-meta">{candidate.category || 'unknown'} · {candidate.review_action || 'manual_review'} · {candidate.candidate_id?.slice(0, 8)}</div>
                    <p>{candidate.content}</p>
                  </div>
                </label>
              )
            })}
          </div>
        )}
      </section>

      <section className="gov-policy">
        <h2>当前 Agent 权限范围</h2>
        <div className="gov-policy-grid">
          <div><span className="allow">允许</span><b>market:read</b><small>读取行情和已记录证据</small></div>
          <div><span className="allow">允许</span><b>document:read</b><small>读取本次会话上传文档</small></div>
          <div><span className="allow">允许</span><b>memory:read</b><small>只读已批准长期记忆</small></div>
          <div><span className="deny">禁止</span><b>publish / trade</b><small>必须经过输出门和人工审核；系统未绑定交易执行</small></div>
        </div>
        <p className="gov-index-note">批准候选会生成 approved Markdown，但不会自动写入 pgvector；完成审核后仍需运行 <code>python scripts/sync_memory_index.py</code>。</p>
      </section>

      {dialog?.type === 'full' && <FullAccessDialog loading={loading} onCancel={() => setDialog(null)} onConfirm={ttl => changeMode('full_access', ttl)} />}
      {dialog?.type === 'batch' && <BatchDialog count={selected.length} loading={loading} onCancel={() => setDialog(null)} onConfirm={reviewSelected} />}
    </div>
  )
}
