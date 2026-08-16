import { useState, useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth, useModal } from '../store'
import { api } from '../api'
import { SECTORS, SCAN_STRATEGIES } from '../constants'

function AuthGate() {
  const openModal = useModal(s => s.open)
  return (
    <div className="auth-gate">
      <p style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>请先登录后使用选股功能</p>
      <button className="btn-start" style={{ marginTop: 8 }} onClick={() => openModal('login')}>立即登录</button>
    </div>
  )
}

function Spinner() {
  return (
    <div className="func-card">
      <div className="spinner-wrap"><div className="spinner" />处理中，请稍候…</div>
    </div>
  )
}

function EmptyState({ icon, msg }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <p>{msg}</p>
    </div>
  )
}

function ScoreBar({ score, label }) {
  return (
    <div style={{ width: 140 }}>
      <div className="score-bar-label">
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>{label}</span>
        <span style={{ fontWeight: 700, color: 'var(--purple)' }}>{score}</span>
      </div>
      <div className="score-bar"><div className="score-bar-fill" style={{ width: `${score}%` }} /></div>
    </div>
  )
}

/* ── Tab: 今日买点 ── */
function ScanTab() {
  const [strategy, setStrategy] = useState('all')
  const [baseStart, setBaseStart] = useState('20230101')
  const [topN, setTopN] = useState(10)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState(null)
  const [error, setError] = useState('')

  async function run() {
    setError(''); setResults(null); setLoading(true)
    try {
      const d = await api.scanToday({ base_start: baseStart, top_n: topN, strategy })
      setResults(d.recommendations || d.results || [])
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  return (
    <>
      <div className="func-card">
        <h3>扫描参数</h3>
        <div className="form-row cols-2" style={{ marginBottom: 16 }}>
          <div className="form-group">
            <label>扫描策略</label>
            <select value={strategy} onChange={e => setStrategy(e.target.value)}>
              {SCAN_STRATEGIES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label>数据起始日期</label>
            <input value={baseStart} onChange={e => setBaseStart(e.target.value)} placeholder="YYYYMMDD" />
          </div>
        </div>
        <div className="form-row cols-2" style={{ marginBottom: 16 }}>
          <div className="form-group">
            <label>结果上限 <span style={{ color: 'var(--purple)', fontWeight: 700 }}>{topN}</span></label>
            <div className="slider-wrap">
              <input type="range" min="5" max="30" value={topN} onChange={e => setTopN(+e.target.value)} />
              <span className="slider-val">{topN}</span>
            </div>
          </div>
          <div className="form-group">
            <label>策略说明</label>
            <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.7, paddingTop: 4 }}>
              超卖：KDJ J值低于15，适合抄底<br />
              金叉：K线上穿J线且低位，适合趋势跟随
            </div>
          </div>
        </div>
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? '扫描中…' : '📡 发射扫描'}
        </button>
      </div>

      {loading && <Spinner />}
      {error && <div className="func-card"><div style={{ color: '#dc2626' }}>❌ {error}</div></div>}

      {results && !loading && (
        <div className="func-card">
          <h3>扫描结果</h3>
          {results.length === 0
            ? <EmptyState icon="📡" msg="暂无触发信号" />
            : results.map((x, i) => (
              <div key={i} className="stock-result-card" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 10 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
                  <div>
                    <div className="stock-result-name">{x.name || x.stock_name} ({x.code || x.stock_code})</div>
                    <div className="stock-result-code">触发日价格：¥{x.close || x.price || '-'}</div>
                  </div>
                  <span className={`badge ${x.confidence === '高' ? 'badge-buy' : 'badge-hold'}`}>
                    {x.confidence || '-'}置信度
                  </span>
                </div>
                {x.decision && <div style={{ fontSize: 13, color: 'var(--slate)', lineHeight: 1.6 }}>{x.decision}</div>}
              </div>
            ))}
        </div>
      )}
    </>
  )
}

/* ── Tab: 板块筛选 ── */
function FilterTab() {
  const [sector, setSector] = useState(SECTORS[0])
  const [minScore, setMinScore] = useState(65)
  const [topN, setTopN] = useState(10)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState(null)
  const [error, setError] = useState('')

  async function run() {
    setError(''); setResults(null); setLoading(true)
    try {
      const d = await api.filterSector({ sector, min_score: minScore, top_n: topN })
      setResults(d.results || [])
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  return (
    <>
      <div className="func-card">
        <h3>筛选参数</h3>
        <div className="form-row cols-2" style={{ marginBottom: 16 }}>
          <div className="form-group">
            <label>监控板块</label>
            <select value={sector} onChange={e => setSector(e.target.value)}>
              {SECTORS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label>结果数量上限</label>
            <select value={topN} onChange={e => setTopN(+e.target.value)}>
              <option value={5}>5 个</option>
              <option value={10}>10 个</option>
              <option value={20}>20 个</option>
            </select>
          </div>
        </div>
        <div className="form-row cols-2" style={{ marginBottom: 16 }}>
          <div className="form-group">
            <label>综合评分及格线 <span style={{ color: 'var(--purple)', fontWeight: 700 }}>{minScore}</span></label>
            <div className="slider-wrap">
              <input type="range" min="50" max="90" value={minScore} onChange={e => setMinScore(+e.target.value)} />
              <span className="slider-val">{minScore}</span>
            </div>
          </div>
        </div>
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? '筛选中…' : '🔍 执行筛选'}
        </button>
      </div>

      {loading && <Spinner />}
      {error && <div className="func-card"><div style={{ color: '#dc2626' }}>❌ {error}</div></div>}

      {results && !loading && (
        <div className="func-card">
          <h3>筛选结果</h3>
          {results.length === 0
            ? <EmptyState icon="🔍" msg="暂无达到评级的标的" />
            : results.map((x, i) => (
              <div key={i} className="stock-result-card">
                <div style={{ flex: 1 }}>
                  <div className="stock-result-name">{x.name || x.stock_name}</div>
                  <div className="stock-result-code" style={{ display: 'flex', gap: 16, marginTop: 4, flexWrap: 'wrap' }}>
                    <span>{x.code || x.stock_code}</span>
                    {x.pe != null && <span>PE: {x.pe.toFixed(1)}</span>}
                    {x.roe != null && <span>ROE: {x.roe.toFixed(1)}%</span>}
                    {x.market_cap != null && <span>市值: {(x.market_cap / 1e8).toFixed(0)}亿</span>}
                    {x.change_pct != null && (
                      <span style={{ color: x.change_pct >= 0 ? '#16a34a' : '#dc2626' }}>
                        {x.change_pct >= 0 ? '+' : ''}{x.change_pct.toFixed(2)}%
                      </span>
                    )}
                  </div>
                </div>
                <ScoreBar score={x.score || x.total_score || 0} label="综合分" />
              </div>
            ))}
        </div>
      )}
    </>
  )
}

/* ── Tab: 个股评分 ── */
function AlphaTab() {
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  async function run() {
    if (!stockCode) { setError('请输入股票代码'); return }
    setError(''); setResult(null); setLoading(true)
    try {
      const d = await api.alphaSingle(stockCode, stockName || stockCode)
      setResult(d)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  const score = result?.total_score ?? result?.score ?? null
  const rating = result?.rating ?? null

  return (
    <>
      <div className="func-card">
        <h3>个股多维评分</h3>
        <p style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 16 }}>输入股票代码，AI 对该标的进行多维度综合评分</p>
        <div className="form-row cols-2">
          <div className="form-group">
            <label>股票代码</label>
            <input value={stockCode} onChange={e => setStockCode(e.target.value)}
              placeholder="如 600150" onKeyDown={e => e.key === 'Enter' && run()} />
          </div>
          <div className="form-group">
            <label>股票名称（可选）</label>
            <input value={stockName} onChange={e => setStockName(e.target.value)}
              placeholder="如 中国船舶" onKeyDown={e => e.key === 'Enter' && run()} />
          </div>
        </div>
        {error && <div style={{ color: '#dc2626', fontSize: 13, marginBottom: 8 }}>{error}</div>}
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? '评分中…' : '✨ 开始评分'}
        </button>
      </div>

      {loading && <Spinner />}

      {result && !loading && (
        <div className="func-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
            <div>
              <div className="stock-result-name" style={{ fontSize: 18 }}>
                {result.stock_name || stockName || stockCode}
              </div>
              <div className="stock-result-code">{result.stock_code || stockCode}</div>
            </div>
            {rating && (
              <span className={`badge ${score >= 75 ? 'badge-buy' : score >= 60 ? 'badge-hold' : 'badge-sell'}`} style={{ fontSize: 14, padding: '6px 16px' }}>
                {rating}
              </span>
            )}
          </div>

          {score != null && (
            <div style={{ marginBottom: 20 }}>
              <ScoreBar score={score} label="综合评分" />
            </div>
          )}

          {/* dimension scores */}
          {['technical_score', 'fundamental_score', 'momentum_score', 'sentiment_score'].map(key => {
            if (result[key] == null) return null
            const labels = { technical_score: '技术面', fundamental_score: '基本面', momentum_score: '动量', sentiment_score: '情绪面' }
            return <ScoreBar key={key} score={result[key]} label={labels[key]} />
          })}

          {/* analysis text */}
          {result.analysis && (
            <div style={{ marginTop: 16, padding: '14px 16px', background: 'var(--bg2)', borderRadius: 10, fontSize: 13.5, lineHeight: 1.8, color: 'var(--slate)', whiteSpace: 'pre-wrap' }}>
              {result.analysis}
            </div>
          )}

          {/* tags */}
          {result.tags && result.tags.length > 0 && (
            <div className="tag-list" style={{ marginTop: 12 }}>
              {result.tags.map((t, i) => <span key={i} className="tag">{t}</span>)}
            </div>
          )}
        </div>
      )}
    </>
  )
}

/* ── Main Stock Page ── */
export default function Stock() {
  const { token } = useAuth()
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const defaultTab = searchParams.get('tab') || 'scan'
  const [tab, setTab] = useState(defaultTab)

  useEffect(() => {
    const t = new URLSearchParams(location.search).get('tab')
    if (t) setTab(t)
  }, [location.search])

  if (!token) return (
    <div className="func-page">
      <AuthGate />
    </div>
  )

  const tabs = [
    { key: 'scan', label: '🎯 今日买点' },
    { key: 'filter', label: '🔍 板块筛选' },
    { key: 'alpha', label: '✨ 个股评分' },
  ]

  return (
    <div className="func-page">
      <div className="func-header">
        <h1>选股中心</h1>
        <p>今日买点扫描 · 板块综合筛选 · 个股多维评分</p>
      </div>
      <div className="func-body">
        <div className="tabs-nav">
          {tabs.map(t => (
            <button key={t.key} className={`tab-btn${tab === t.key ? ' active' : ''}`} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        {tab === 'scan' && <ScanTab />}
        {tab === 'filter' && <FilterTab />}
        {tab === 'alpha' && <AlphaTab />}
      </div>
    </div>
  )
}
