import { useEffect, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth, useModal } from '../store'

export default function Home() {
  const { token } = useAuth()
  const openModal = useModal(s => s.open)
  const navigate = useNavigate()
  const location = useLocation()

  const [cfName, setCfName] = useState('')
  const [cfEmail, setCfEmail] = useState('')
  const [cfMsg, setCfMsg] = useState('')
  const [cfSent, setCfSent] = useState(false)

  useEffect(() => {
    window.scrollTo(0, 0)
    if (location.state?.scrollTo) {
      setTimeout(() => {
        document.getElementById(location.state.scrollTo)?.scrollIntoView({ behavior: 'smooth' })
      }, 100)
    }
  }, [location.state])

  function goProtected(path) {
    if (!token) { openModal('login'); return }
    navigate(path)
  }

  function sendContact() {
    if (!cfName || !cfEmail || !cfMsg) { alert('请填写完整信息'); return }
    window.location.href = `mailto:yulingyu08@gmail.com?subject=AlphaStock 反馈 - ${cfName}&body=${encodeURIComponent(cfMsg + '\n\n来自: ' + cfEmail)}`
    setCfSent(true)
  }

  return (
    <div className="page-home">
      {/* ── Hero ── */}
      <div className="hero">
        <div className="hero-left">
          <div className="hero-badge"><div className="dot" />&nbsp;A 股智能投研 · 多维分析引擎</div>
          <h1>Meet Your AI<br /><span className="grad">Trading Partner</span></h1>
          <p className="hero-desc">
            专为 A 股投资者设计的智能投研平台。输入一个股票代码，AI 从多个视角同时分析，
            几分钟内给出买入、观望或减仓的明确建议。
          </p>
          <div className="hero-cta">
            <button className="btn-hero" onClick={() => goProtected('/chat')}>开始分析</button>
          </div>
          <div className="hero-label">六大工具 · 一站投研</div>
          <div className="hero-tags">
            <span className="hero-tag"><span className="tag-dot" />AI 研报生成</span>
            <span className="hero-tag">量化回测</span>
            <span className="hero-tag">Alpha 选股</span>
            <span className="hero-tag">今日买点</span>
            <span className="hero-tag">板块筛选</span>
            <span className="hero-tag">多视角辩论决策</span>
          </div>
        </div>

        <div className="hero-right">
          <div className="hero-card">
            <div className="hero-card-header">
              <div className="hc-dots">
                <div className="hc-dot" style={{ background: '#ff5f56' }} />
                <div className="hc-dot" style={{ background: '#febc2e' }} />
                <div className="hc-dot" style={{ background: '#27c93f' }} />
              </div>
              <span className="hc-title">AlphaStock · AI 分析引擎</span>
            </div>
            <div className="hero-card-body">
              <div className="hc-stock-row">
                <div><div className="hc-stock-name">中国船舶</div><div className="hc-stock-code">600150 · 造船</div></div>
                <span className="hc-badge-buy">🔴 买入建议</span>
              </div>
              <div className="hc-stock-row">
                <div><div className="hc-stock-name">中际旭创</div><div className="hc-stock-code">300308 · 光模块</div></div>
                <span className="hc-badge-hold">🟡 观望状态</span>
              </div>
              <div className="hc-metrics">
                <div className="hc-metric"><div className="hc-metric-val up">+24.6%</div><div className="hc-metric-label">策略收益</div></div>
                <div className="hc-metric"><div className="hc-metric-val">2.14</div><div className="hc-metric-label">夏普比率</div></div>
                <div className="hc-metric"><div className="hc-metric-val">78%</div><div className="hc-metric-label">胜率</div></div>
              </div>
              <div className="hc-analysis">
                <span className="hc-tag">[多头视角]</span> 订单饱满，景气上行<br />
                <span className="hc-tag">[空头视角]</span> 短期回调风险需关注<br />
                <span className="hc-tag">[技术面]</span> KDJ 超卖，底部支撑有效<br />
                <span className="hc-val">[综合决策]</span> 建议买入，仓位 30%
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Intro ── */}
      <section id="intro" className="section-intro">
        <div className="section-inner">
          <div className="section-eyebrow">About This Project</div>
          <h2 className="section-title">为什么选择 AlphaStock</h2>
          <p className="section-desc">一个人的精力有限，但市场每天都在变化。AlphaStock 帮你从海量数据中找到真正值得关注的机会，让决策更有依据。</p>
          <div className="intro-grid">
            {[
              { icon: '🧠', title: '多视角交叉验证', desc: '系统同时从基本面、技术面、市场情绪三个维度分析同一标的，多头与空头视角相互辩论，最终给出综合决策，而不是单一指标的片面判断。' },
              { icon: '📊', title: '策略回测验证', desc: '在真实历史数据上验证交易策略的有效性，查看收益曲线、最大回撤和胜率，用数据说话，避免凭感觉操作。' },
              { icon: '✨', title: '全市场自动筛选', desc: '不需要一只一只看，系统自动扫描全市场，根据多项指标打分排序，把符合条件的标的直接呈现给你，节省大量时间。' },
              { icon: '⚡', title: '实时数据驱动', desc: '接入实时行情数据，分析结果基于最新市场状态，不是过时的信息。支持上传财报截图，AI 自动提取关键数据纳入分析。' },
            ].map(c => (
              <div key={c.title} className="intro-card">
                <div className="intro-card-icon">{c.icon}</div>
                <div className="intro-card-title">{c.title}</div>
                <div className="intro-card-desc">{c.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Product overview */}
      <section className="section-products">
        <div className="section-inner">
          <div className="products-heading">
            <div>
              <div className="section-eyebrow">One platform, full workflow</div>
              <h2 className="section-title">把研究、验证与决策<br />放进同一套工作流</h2>
            </div>
            <p className="products-lead">从捕捉市场机会，到形成可执行的交易计划，AlphaStock 用统一的分析语言把每一步串联起来。</p>
          </div>
          <div className="products-grid">
            {[
              { no: '01', icon: '⌁', title: 'AI 投研对话', desc: '围绕股票、行业和市场问题连续追问，让复杂信息快速沉淀成清晰结论。', action: '进入 AI 助手', path: '/chat' },
              { no: '02', icon: '↗', title: '策略回测', desc: '用历史数据检查策略的收益、回撤和胜率，先验证，再决定是否执行。', action: '查看回测工具', path: '/backtest' },
              { no: '03', icon: '◎', title: 'Alpha 选股', desc: '结合多因子评分、板块筛选与今日买点，把值得研究的标的优先呈现。', action: '开始选股', path: '/stock?tab=alpha' },
            ].map(item => (
              <article className="product-card" key={item.no}>
                <div className="product-card-top"><span className="product-number">{item.no}</span><span className="product-icon">{item.icon}</span></div>
                <h3>{item.title}</h3>
                <p>{item.desc}</p>
                <button onClick={() => goProtected(item.path)}>{item.action} <span>→</span></button>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Workflow */}
      <section className="section-workflow">
        <div className="section-inner workflow-layout">
          <div className="workflow-copy">
            <div className="section-eyebrow">Research workflow</div>
            <h2 className="section-title">不是给一条结论，<br />而是给你完整依据。</h2>
            <p className="section-desc">每一个建议都应该能追溯到数据、逻辑与风险边界。AlphaStock 把研究过程透明地呈现在你面前。</p>
            <button className="btn-workflow" onClick={() => goProtected('/chat')}>体验完整投研流程 <span>→</span></button>
          </div>
          <div className="workflow-panel">
            {[
              ['01', '发现机会', '扫描今日买点、板块热度与多因子评分，锁定优先级更高的标的。'],
              ['02', '多维研究', '从基本面、技术面、情绪面交叉核验，记录多头与空头的关键观点。'],
              ['03', '量化验证', '将策略放进历史行情，衡量收益、风险和适用条件。'],
              ['04', '形成计划', '输出可执行的仓位、目标、止损与风险提示，辅助你做最后判断。'],
            ].map(([no, title, desc]) => (
              <div className="workflow-step" key={no}>
                <span className="workflow-no">{no}</span>
                <div><h3>{title}</h3><p>{desc}</p></div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Stats ── */}
      <section id="stats" className="section-stats">
        <div className="stats-grid">
          {[['5', '内置量化策略'], ['6', '分析工具'], ['3', '推理引擎可选'], ['全市场', 'A 股覆盖']].map(([v, l]) => (
            <div key={l} className="stat-item">
              <div className="stat-val">{v}</div>
              <div className="stat-label">{l}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Final call to action */}
      <section className="section-cta">
        <div className="section-inner cta-inner">
          <div>
            <div className="section-eyebrow">Start with one question</div>
            <h2>从下一次研究开始，<br />让决策更有依据。</h2>
            <p>输入股票代码，或直接问一个你正在关注的市场问题。</p>
          </div>
          <button className="btn-cta" onClick={() => goProtected('/chat')}>进入 Alpha AI <span>→</span></button>
        </div>
      </section>

      {/* ── Contact ── */}
      <section id="contact" className="section-contact">
        <div className="section-inner">
          <div className="section-eyebrow">Contact</div>
          <h2 className="section-title">联系我们</h2>
          <div className="contact-layout">
            <div>
              <div className="contact-info-title">有任何问题都可以联系</div>
              <div className="contact-info-desc">产品反馈、合作洽谈或技术问题，会在 24 小时内回复。</div>
              <div className="contact-links">
                <a href="mailto:yulingyu08@gmail.com" className="contact-link">
                  <span className="contact-link-icon">✉️</span>
                  <div><div className="contact-link-text">Email</div><div className="contact-link-sub">yulingyu08@gmail.com</div></div>
                </a>
                <a href="https://github.com/Neon549" target="_blank" rel="noreferrer" className="contact-link">
                  <span className="contact-link-icon">🐙</span>
                  <div><div className="contact-link-text">GitHub</div><div className="contact-link-sub">查看源码</div></div>
                </a>
              </div>
            </div>
            <div className="contact-form-card">
              <div className="cf-title">发送消息</div>
              <div className="cf-group">
                <label className="cf-label">姓名</label>
                <input className="cf-input" placeholder="你的名字" value={cfName} onChange={e => setCfName(e.target.value)} />
              </div>
              <div className="cf-group">
                <label className="cf-label">邮箱</label>
                <input className="cf-input" type="email" placeholder="your@email.com" value={cfEmail} onChange={e => setCfEmail(e.target.value)} />
              </div>
              <div className="cf-group">
                <label className="cf-label">消息</label>
                <textarea className="cf-textarea" placeholder="说说你的想法…" value={cfMsg} onChange={e => setCfMsg(e.target.value)} />
              </div>
              <button className="cf-submit" onClick={sendContact}>发送消息</button>
              {cfSent && <div style={{ marginTop: 14, color: '#16a34a', fontSize: 14, fontWeight: 600, textAlign: 'center' }}>✓ 消息已发送！</div>}
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer>
        <div className="footer-inner">
          <div className="footer-top">
            <div>
              <div className="footer-logo"><div className="logo-icon">A</div>AlphaStock</div>
              <div className="footer-brand-desc">专为 A 股投资者设计的智能投研平台。工具仅供参考，不构成投资建议，投资有风险，入市需谨慎。</div>
            </div>
            <div>
              <div className="footer-col-title">Product</div>
              <div className="footer-links">
                <button onClick={() => goProtected('/chat')}>Alpha AI 助手</button>
                <button onClick={() => goProtected('/backtest')}>量化回测</button>
                <button onClick={() => goProtected('/stock?tab=alpha')}>Alpha 选股</button>
                <button onClick={() => goProtected('/stock?tab=scan')}>今日买点</button>
                <button onClick={() => goProtected('/stock?tab=filter')}>板块筛选</button>
              </div>
            </div>
            <div>
              <div className="footer-col-title">Company</div>
              <div className="footer-links">
                <a onClick={() => document.getElementById('intro')?.scrollIntoView({ behavior: 'smooth' })}>项目介绍</a>
                <a onClick={() => document.getElementById('contact')?.scrollIntoView({ behavior: 'smooth' })}>联系我们</a>
                <a href="mailto:yulingyu08@gmail.com">合作洽谈</a>
              </div>
            </div>
            <div>
              <div className="footer-col-title">Legal</div>
              <div className="footer-links">
                <a href="#">隐私政策</a>
                <a href="#">使用条款</a>
                <a href="#">免责声明</a>
              </div>
            </div>
          </div>
          <div className="footer-bottom">
            <div className="footer-copy">© 2026 AlphaStock · yulingyu08@gmail.com</div>
            <div className="footer-socials">
              <a href="mailto:yulingyu08@gmail.com" className="social-btn">✉</a>
              <a href="https://github.com/Neon549" target="_blank" rel="noreferrer" className="social-btn">⌥</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  )
}
