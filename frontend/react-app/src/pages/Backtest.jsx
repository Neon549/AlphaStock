import { useState, useEffect, useRef } from 'react'
import {
  Chart as ChartJS,
  CategoryScale, LinearScale, PointElement, LineElement,
  Filler, Tooltip, Legend,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import { useAuth, useModal } from '../store'
import { api } from '../api'
import { STRATEGIES } from '../constants'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip, Legend)

function AuthGate() {
  const openModal = useModal(s => s.open)
  return (
    <div className="auth-gate">
      <p style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>请先登录后使用量化回测</p>
      <button className="btn-start" style={{ marginTop: 8 }} onClick={() => openModal('login')}>立即登录</button>
    </div>
  )
}

export default function Backtest() {
  const { token } = useAuth()
  const [code, setCode] = useState('600150')
  const [strategy, setStrategy] = useState('kdj_oversold')
  const [startDate, setStartDate] = useState('20240101')
  const [endDate, setEndDate] = useState('20260530')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [strategies, setStrategies] = useState(STRATEGIES)

  useEffect(() => {
    api.getStrategies()
      .then(d => {
        if (Array.isArray(d) && d.length > 0) setStrategies(d)
      })
      .catch(() => {})
  }, [])

  async function run() {
    setError('')
    setResult(null)
    setLoading(true)
    try {
      const d = await api.backtest({
        stock_code: code,
        strategy,
        start_date: startDate,
        end_date: endDate,
        initial_cash: 100000,
      })
      setResult(d)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // build cumulative returns chart data
  const chartData = (() => {
    if (!result?.returns_data || !result?.dates_data) return null
    const step = Math.max(1, Math.floor(result.returns_data.length / 200))
    let cum = 1
    const cumRets = result.returns_data.map(r => {
      cum *= (1 + r)
      return +((cum - 1) * 100).toFixed(3)
    })
    const labels = result.dates_data.filter((_, i) => i % step === 0)
    const data = cumRets.filter((_, i) => i % step === 0)
    const positive = data[data.length - 1] >= 0
    return {
      labels,
      datasets: [{
        data,
        borderColor: positive ? '#16a34a' : '#dc2626',
        backgroundColor: positive ? 'rgba(22,163,74,0.08)' : 'rgba(220,38,38,0.08)',
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.3,
      }],
    }
  })()

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: ctx => `收益率: ${ctx.raw.toFixed(2)}%` } },
    },
    scales: {
      x: { ticks: { maxTicksLimit: 8, font: { size: 11 } }, grid: { display: false } },
      y: { ticks: { callback: v => v + '%', font: { size: 11 } }, grid: { color: '#f1f5f9' } },
    },
  }

  const metrics = result ? [
    { label: '总收益', value: (result.total_return >= 0 ? '+' : '') + (result.total_return ?? 0).toFixed(2) + '%', up: result.total_return >= 0, down: result.total_return < 0 },
    { label: '夏普比率', value: result.sharpe ?? '-' },
    { label: '最大回撤', value: '-' + (result.max_drawdown ?? 0).toFixed(2) + '%', down: true },
    { label: '交易次数', value: result.trade_count ?? '-' },
    { label: '胜率', value: (result.win_rate ?? '-') + (result.win_rate != null ? '%' : '') },
  ] : []

  if (!token) return (
    <div className="func-page">
      <AuthGate />
    </div>
  )

  return (
    <div className="func-page">
      <div className="func-header">
        <h1>📊 量化回测</h1>
        <p>选择策略和时间范围，在历史数据上验证交易逻辑</p>
      </div>
      <div className="func-body">
        <div className="func-card">
          <h3>回测参数</h3>
          <div className="form-row cols-4">
            <div className="form-group">
              <label>目标标的代码</label>
              <input value={code} onChange={e => setCode(e.target.value)} placeholder="如 600150" />
            </div>
            <div className="form-group">
              <label>核心策略</label>
              <select value={strategy} onChange={e => setStrategy(e.target.value)}>
                {strategies.map(s => (
                  <option key={s.value || s} value={s.value || s}>
                    {s.label || s}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>回测起点</label>
              <input value={startDate} onChange={e => setStartDate(e.target.value)} placeholder="YYYYMMDD" />
            </div>
            <div className="form-group">
              <label>回测终点</label>
              <input value={endDate} onChange={e => setEndDate(e.target.value)} placeholder="YYYYMMDD" />
            </div>
          </div>
          <button className="btn-primary" onClick={run} disabled={loading}>
            {loading ? '运行中…' : '🚀 启动回测'}
          </button>
        </div>

        {loading && (
          <div className="func-card">
            <div className="spinner-wrap">
              <div className="spinner" />
              正在加载历史数据并运行策略，请稍候…
            </div>
          </div>
        )}

        {error && (
          <div className="func-card">
            <div style={{ color: '#dc2626', fontSize: 14 }}>❌ {error}</div>
          </div>
        )}

        {result && !loading && (
          <div className="func-card">
            <h3>回测结果</h3>
            <div className="result-grid">
              {metrics.map(m => (
                <div key={m.label} className="result-metric">
                  <div className={`val${m.up ? ' up' : m.down ? ' down' : ''}`}>{m.value}</div>
                  <div className="lbl">{m.label}</div>
                </div>
              ))}
            </div>
            <div className="chart-wrap">
              {chartData ? (
                <Line data={chartData} options={chartOptions} />
              ) : (
                <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg2)', borderRadius: 10, color: 'var(--muted)', fontSize: 14 }}>
                  暂无收益曲线数据
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
