import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, useModal } from '../store'
import { api } from '../api'
import { MODELS } from '../constants'

const APPROVAL_MODES = [
  { key: 'safe', label: '安全模式', icon: '🛡️', description: '所有记忆候选等待审核', color: '#2563eb' },
  { key: 'assist', label: '帮我审批', icon: '✨', description: '低风险自动处理，其余批量确认', color: '#7c3aed' },
  { key: 'full_access', label: '完全访问权限', icon: '⚠️', description: '短时授权，硬阻断仍然有效', color: '#ea580c' },
]

function ApprovalModePanel({ modeState, onModeChange, onOpenGovernance, error }) {
  const [popoverOpen, setPopoverOpen] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [ttl, setTtl] = useState(30)
  const [confirmed, setConfirmed] = useState(false)
  const [saving, setSaving] = useState(false)
  const current = modeState?.mode || 'safe'
  const currentMeta = APPROVAL_MODES.find(item => item.key === current) || APPROVAL_MODES[0]

  async function selectMode(nextMode) {
    try {
      await onModeChange(nextMode)
      setPopoverOpen(false)
    } catch {
      // The parent exposes the backend error inside this popover.
    }
  }

  async function confirmFullAccess() {
    setSaving(true)
    try {
      await onModeChange('full_access', ttl)
      setDialogOpen(false)
      setConfirmed(false)
      setPopoverOpen(false)
    } catch {
      // The parent renders the backend error beside the permission controls.
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="cl-permission-anchor">
      <button
        className="cl-tool-btn cl-permission-trigger"
        onClick={() => setPopoverOpen(value => !value)}
        title="权限与审核"
        aria-label="权限与审核"
        aria-expanded={popoverOpen}
      >
        <span aria-hidden="true">🛡</span>
        <span className="cl-permission-status">{currentMeta.label}</span>
      </button>

      {popoverOpen && (
        <div className="cl-permission-panel" role="dialog" aria-label="权限与审核">
          <div className="cl-permission-title"><span>权限与审核</span><button onClick={() => { setPopoverOpen(false); onOpenGovernance() }}>管理 →</button></div>
          <div className="cl-permission-current">
            <span className="cl-permission-dot" style={{ background: currentMeta.color }} />
            当前：<strong>{currentMeta.label}</strong>
            {modeState?.elevated_expires_at && <small> · {modeState.elevated_expires_at} 到期</small>}
          </div>
          <div className="cl-permission-buttons">
            {APPROVAL_MODES.map(item => (
              <button
                key={item.key}
                className={current === item.key ? 'active' : ''}
                style={current === item.key ? { borderColor: item.color, color: item.color } : undefined}
                onClick={() => item.key === 'full_access' ? setDialogOpen(true) : selectMode(item.key)}
              >
                {item.icon} {item.label}
              </button>
            ))}
          </div>
          <div className="cl-permission-policy"><span>✓ 只读研究</span><span>✓ 文档检索</span><span>🔒 发布/交易需人工审核</span></div>
          {error && <div className="cl-permission-error">{error}</div>}
        </div>
      )}

      {dialogOpen && (
        <div className="cl-access-dialog-backdrop" role="presentation" onMouseDown={e => e.target === e.currentTarget && setDialogOpen(false)}>
          <div className="cl-access-dialog" role="dialog" aria-modal="true">
            <div className="cl-access-icon">⚠️</div>
            <h3>启用完全访问权限？</h3>
            <p>此模式只提升长期记忆的自动审核范围，不会开放交易下单，也不能绕过实时证据、隐私和高风险硬阻断。</p>
            <ul><li>低/中风险候选可自动处理</li><li>高风险候选仍需一次确认</li><li>授权到期后自动回到安全模式</li></ul>
            <label>授权时长<select value={ttl} onChange={e => setTtl(Number(e.target.value))}>{[15, 30, 60, 120].map(v => <option key={v} value={v}>{v} 分钟</option>)}</select></label>
            <label className="cl-access-check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我已了解风险并确认启用</label>
            <div className="cl-access-actions"><button onClick={() => setDialogOpen(false)}>取消</button><button className="primary" disabled={!confirmed || saving} onClick={confirmFullAccess}>{saving ? '启用中…' : '确认启用'}</button></div>
          </div>
        </div>
      )}
    </div>
  )
}

function renderMd(text) {
  if (!text || text === '[SKIPPED] 本次分析未启用此维度') {
    return <span style={{ color: '#94a3b8', fontSize: 13 }}>本次未启用此维度分析</span>
  }
  return text.split('\n').map((line, i) => {
    if (line.startsWith('### ')) return <h4 key={i} style={{ margin: '12px 0 4px', fontSize: 14, fontWeight: 700 }}>{line.slice(4)}</h4>
    if (line.startsWith('## ')) return <h3 key={i} style={{ margin: '14px 0 6px', fontSize: 15, fontWeight: 700 }}>{line.slice(3)}</h3>
    if (line.startsWith('# ')) return <h2 key={i} style={{ margin: '16px 0 8px', fontSize: 16, fontWeight: 800 }}>{line.slice(2)}</h2>
    if (line.trim() === '') return <br key={i} />
    const parts = line.split(/(\*\*[^*]+\*\*)/)
    return (
      <p key={i} style={{ margin: '3px 0', fontSize: 13.5, lineHeight: 1.75, color: '#374151' }}>
        {parts.map((part, j) =>
          part.startsWith('**') && part.endsWith('**')
            ? <strong key={j} style={{ color: '#111827', fontWeight: 600 }}>{part.slice(2, -2)}</strong>
            : part
        )}
      </p>
    )
  })
}

function AnalyzeCard({ data }) {
  const [tab, setTab] = useState('decision')
  const isObj = typeof data === 'object' && data !== null
  const sections = isObj ? {
    decision: data.decision || '',
    technical: data.technical_report || '',
    fundamental: data.fundamental_report || '',
    sentiment: data.sentiment_report || '',
    researcher: data.researcher_analysis || data.bull_argument || '',
  } : { decision: String(data) }

  const stockCode = isObj ? (data.stock_code || '') : ''
  const stockName = isObj ? (data.stock_name || '') : ''
  const citations = isObj && Array.isArray(data.document_citations) ? data.document_citations : []
  const evidenceCards = isObj && Array.isArray(data.evidence_cards) ? data.evidence_cards : []
  const traceSummary = isObj ? data.trace_summary : null
  const isBuy = sections.decision.includes('买入')
  const isSell = sections.decision.includes('卖出') || sections.decision.includes('减仓')

  const tabs = [
    { key: 'decision', label: '综合结论' },
    { key: 'technical', label: '技术面' },
    { key: 'fundamental', label: '基本面' },
    { key: 'sentiment', label: '情绪面' },
    { key: 'researcher', label: '多空辩论' },
  ]

  return (
    <div className="ac-wrap">
      <div className="ac-header">
        <div className="ac-header-left">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
          <span>投研报告</span>
          {(stockName || stockCode) && <span className="ac-stock">{stockName} {stockCode}</span>}
        </div>
        <span className={`ac-badge ${isBuy ? 'ac-buy' : isSell ? 'ac-sell' : 'ac-hold'}`}>
          {isBuy ? '买入' : isSell ? '减仓' : '观望'}
        </span>
      </div>

      <div className="ac-tabs">
        {tabs.map(t => (
          <button key={t.key} className={`ac-tab${tab === t.key ? ' active' : ''}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="ac-body">
        {renderMd(sections[tab])}
      </div>
      {citations.length > 0 && (
        <div className="ac-citations">
          <div className="ac-citations-title">文档依据</div>
          {citations.map(citation => (
            <div className="ac-citation" key={citation.evidence_id}>
              <span className="ac-citation-file">{citation.filename || '已上传文档'}</span>
              <span>{citation.section || '正文'}</span>
              {citation.page && <span>第 {citation.page} 页</span>}
            </div>
          ))}
        </div>
      )}
      {evidenceCards.length > 0 && (
        <div className="ac-evidence-cards">
          <div className="ac-citations-title">数据证据</div>
          {evidenceCards.map(card => (
            <div className="ac-evidence-card" key={card.evidence_id}>
              <div className="ac-evidence-title">
                <strong>{card.title || '财务摘要'}</strong>
                <span className={`ac-freshness ${card.usable_for_current_conclusion ? 'current' : 'stale'}`}>
                  {card.usable_for_current_conclusion ? '可作为当前结论依据' : '仅作历史参考'}
                </span>
              </div>
              <div className="ac-evidence-meta">
                <span>来源：{card.data_source || '未知'}</span>
                <span>抓取：{card.retrieved_at || '未知'}</span>
                <span>财报期：{card.report_period || '未知'}</span>
                {Number.isFinite(card.age_days) && <span>距今：{card.age_days} 天</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      <EvidenceStatusSummary
        traceSummary={traceSummary}
        publishStatus={isObj ? data.publish_status : undefined}
        publishReasons={isObj ? data.publish_reasons : []}
        runId={isObj ? data.run_id : undefined}
      />
    </div>
  )
}

const EVIDENCE_STATUS_LABELS = {
  not_requested: '未请求',
  no_hit: '检索无命中',
  success: '已获取',
  error: '拉取/检索失败',
  stale_rejected: '已获取但过期',
  partial: '部分成功',
  not_applicable: '不适用',
  passed: '已通过',
  human_review: '待人工审核',
  blocked: '已阻断',
  received: '已接收',
  empty: '空输入',
  rejected: '已拒绝',
}

function EvidenceStatusSummary({ traceSummary, publishStatus, publishReasons = [], runId }) {
  const status = traceSummary?.evidence_status
  if (!status) return null
  const rows = [
    ['document_rag', '文档 RAG'],
    ['market_data', '行情/财务数据'],
    ['citation_validation', '引用校验'],
    ['output_gate', '安全输出门'],
  ]
  return (
    <div className="cb-evidence-status">
      <div className="cb-evidence-status-title">
        <span>证据链路监控</span>
        {runId && <small title={runId}>run: {runId.slice(0, 8)}</small>}
      </div>
      <div className="cb-evidence-status-grid">
        {rows.map(([key, label]) => {
          const item = status[key] || { status: 'not_applicable' }
          return (
            <div className={`cb-evidence-status-item status-${item.status}`} key={key}>
              <span>{label}</span>
              <strong>{EVIDENCE_STATUS_LABELS[item.status] || item.status}</strong>
              {item.reason && <small>{item.reason}</small>}
            </div>
          )
        })}
      </div>
      {publishStatus === 'blocked' && publishReasons.length > 0 && (
        <div className="cb-evidence-status-reason">阻断原因：{publishReasons.join('；')}</div>
      )}
      <ExecutionStatusSummary traceSummary={traceSummary} />
    </div>
  )
}

function ExecutionStatusSummary({ traceSummary }) {
  const status = traceSummary?.execution_status
  if (!status) return null
  const rows = [
    ['user_input', '用户输入'],
    ['external_http', '外部 HTTPS 请求'],
    ['mcp_tools', 'MCP 工具调用'],
  ]
  return (
    <div className="cb-execution-status">
      <div className="cb-evidence-status-title"><span>执行链路监控</span></div>
      <div className="cb-evidence-status-grid cb-execution-status-grid">
        {rows.map(([key, label]) => {
          const item = status[key] || { status: 'not_requested' }
          return (
            <div className={`cb-evidence-status-item status-${item.status}`} key={key}>
              <span>{label}</span>
              <strong>{EVIDENCE_STATUS_LABELS[item.status] || item.status}</strong>
              {item.reason && <small>{item.reason}</small>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ChatBubble({ msg }) {
  if (msg.role === 'user') {
    return (
      <div className="cb-row cb-user">
        <div className="cb-user-bubble">
          {msg.attachments?.length > 0 && (
            <div className="cb-attachments">
              {msg.attachments.map((f, i) => <span key={i} className="cb-attach">📎 {typeof f === 'string' ? f : f.name}</span>)}
            </div>
          )}
          {msg.content}
        </div>
      </div>
    )
  }
  if (msg.type === 'analyze') {
    return (
      <div className="cb-row cb-ai">
        <div className="cb-ai-avatar">A</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <AnalyzeCard data={msg.content} />
        </div>
      </div>
    )
  }
  return (
    <div className="cb-row cb-ai">
      <div className="cb-ai-avatar">A</div>
      <div className="cb-ai-text">
        {typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)}
        <EvidenceStatusSummary
          traceSummary={msg.traceSummary}
          publishStatus={msg.publishStatus}
          publishReasons={msg.publishReasons}
          runId={msg.runId}
        />
      </div>
    </div>
  )
}

function WelcomeScreen({ username, onSuggest }) {
  const hour = new Date().getHours()
  const greeting = hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好'

  const suggestions = [
    { icon: '📈', title: '分析一支股票', desc: '输入代码获取多维度投研报告', action: '分析股票 600150' },
    { icon: '💡', title: '股票问答', desc: '问我关于市场、选股、策略的问题', action: '什么是市盈率？怎么用它选股？' },
    { icon: '📊', title: '板块分析', desc: '分析某个行业的投资机会', action: '新能源板块现在有哪些投资机会？' },
    { icon: '⚖️', title: '多空对比', desc: '某股票的买入和卖出理由', action: '分析贵州茅台的多空双方观点' },
  ]

  return (
    <div className="welcome-wrap">
      <div className="welcome-greeting">{greeting}，{username || '投资者'}</div>
      <div className="welcome-sub">有什么可以帮你的？</div>
      <div className="welcome-cards">
        {suggestions.map((s, i) => (
          <button key={i} className="welcome-card" onClick={() => onSuggest(s.action)}>
            <div className="wc-icon">{s.icon}</div>
            <div className="wc-title">{s.title}</div>
            <div className="wc-desc">{s.desc}</div>
          </button>
        ))}
      </div>
    </div>
  )
}

function AuthGate() {
  const openModal = useModal(s => s.open)
  return (
    <div className="welcome-wrap">
      <div className="welcome-greeting">欢迎使用 AlphaStock</div>
      <div className="welcome-sub">登录后使用 AI 投研助手</div>
      <button className="btn-start" style={{ marginTop: 24, padding: '12px 32px', fontSize: 15 }} onClick={() => openModal('login')}>
        立即登录
      </button>
    </div>
  )
}

let convIdCounter = Date.now()

function createConversationId() {
  convIdCounter += 1
  return `conv-${Date.now()}-${convIdCounter}`
}

export default function Chat() {
  const { token, username, logout } = useAuth()
  const navigate = useNavigate()
  const { open } = useModal()
  const [conversations, setConversations] = useState([])
  const [currentConvId, setCurrentConvId] = useState(null)
  const [messages, setMessages] = useState([])
  const [mode, setMode] = useState('chat')
  const [input, setInput] = useState('')
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [model, setModel] = useState(MODELS[1].value)
  const [loading, setLoading] = useState(false)
  const [attachments, setAttachments] = useState([])
  const [approvalMode, setApprovalMode] = useState(null)
  const [approvalError, setApprovalError] = useState('')
  const [conversationError, setConversationError] = useState('')
  const messagesEndRef = useRef()
  const inputRef = useRef()
  const fileInputRef = useRef()
  const documentSessionRef = useRef(null)

  useEffect(() => {
    if (!token) return
    api.getApprovalMode().then(setApprovalMode).catch(err => setApprovalError(err.message || '权限状态暂不可用'))
  }, [token])

  async function changeApprovalMode(nextMode, ttl = null) {
    setApprovalError('')
    try {
      const next = await api.setApprovalMode(nextMode === 'full_access'
        ? { mode: nextMode, confirm_risk: true, ttl_minutes: ttl || 30 }
        : { mode: nextMode })
      setApprovalMode(next)
    } catch (err) {
      setApprovalError(err.message || '权限模式切换失败')
      throw err
    }
  }

  useEffect(() => {
    if (token && username) {
      api.getConversations(username)
        .then(data => {
          const remoteConversations = Array.isArray(data) ? data : (data?.conversations || [])
          // Do not replace optimistic drafts created while this request was in
          // flight. The old replacement caused a new conversation to vanish
          // as soon as the initial history request completed.
          setConversations(prev => {
            const remoteIds = new Set(remoteConversations.map(item => item.id || item.conv_id))
            const pendingLocal = prev.filter(item => item.localOnly && !remoteIds.has(item.id || item.conv_id))
            return [...pendingLocal, ...remoteConversations]
          })
        })
        .catch(err => setConversationError(`历史记录暂未同步：${err.message || '请求失败'}`))
    }
  }, [token, username])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function resetDraft() {
    setCurrentConvId(null)
    setMessages([])
    setInput('')
    setStockCode('')
    setStockName('')
    setAttachments([])
    cleanupDocumentSession()
  }

  async function newConversation() {
    const id = createConversationId()
    const conversation = { id, conv_id: id, title: '新对话', messages: [], localOnly: true, syncStatus: 'pending' }
    setCurrentConvId(id)
    setMessages([])
    setInput('')
    setStockCode('')
    setStockName('')
    setAttachments([])
    cleanupDocumentSession()
    setConversationError('')
    setConversations(prev => [conversation, ...prev])

    if (!username) return
    try {
      await api.saveConversation({ id, username, title: conversation.title, messages: [] })
      setConversations(prev => prev.map(item => (item.id || item.conv_id) === id
        ? { ...item, localOnly: false, syncStatus: 'saved' }
        : item))
    } catch (err) {
      // Keep the draft visible and retry when the user sends its first
      // message. A transient auth/network/database error must not erase UI.
      setConversations(prev => prev.map(item => (item.id || item.conv_id) === id
        ? { ...item, syncStatus: 'failed' }
        : item))
      setConversationError(`新对话已创建，但暂未同步到服务器：${err.message || '请求失败'}`)
    }
  }

  function loadConversation(conv) {
    setCurrentConvId(conv.id || conv.conv_id)
    setMessages(conv.messages || [])
  }

  async function deleteConversation(e, conv) {
    e.stopPropagation()
    const id = conv.id || conv.conv_id
    if (conv.localOnly) {
      setConversations(prev => prev.filter(item => (item.id || item.conv_id) !== id))
      if (currentConvId === id) resetDraft()
      return
    }
    try {
      await api.deleteConversation(id)
      setConversations(prev => prev.filter(c => (c.id || c.conv_id) !== id))
      if (currentConvId === id) resetDraft()
    } catch (err) {
      alert(err.message)
    }
  }

  async function saveConversation(msgs) {
    if (!username) return
    const id = currentConvId || createConversationId()
    const title = msgs.find(m => m.role === 'user')?.content?.slice(0, 30) || '新对话'
    try {
      await api.saveConversation({ id, username, title, messages: msgs })
      setCurrentConvId(id)
      setConversations(prev => {
        const exists = prev.find(c => (c.id || c.conv_id) === id)
        if (exists) return prev.map(c => (c.id || c.conv_id) === id
          ? { ...c, messages: msgs, title, localOnly: false, syncStatus: 'saved' }
          : c)
        return [{ id, conv_id: id, title, messages: msgs, localOnly: false, syncStatus: 'saved' }, ...prev]
      })
      setConversationError('')
    } catch (err) {
      console.error('保存对话失败：', err)
      setConversations(prev => prev.map(c => (c.id || c.conv_id) === id ? { ...c, syncStatus: 'failed' } : c))
      setConversationError(`对话内容已保留在当前页面，但暂未同步：${err.message || '请求失败'}`)
    }
  }

  function ensureDocumentSession() {
    if (!documentSessionRef.current) {
      documentSessionRef.current = `web-${crypto.randomUUID()}`
    }
    return documentSessionRef.current
  }

  function cleanupDocumentSession() {
    const sessionId = documentSessionRef.current
    documentSessionRef.current = null
    if (sessionId) api.cleanupDocumentSession(sessionId).catch(() => {})
  }

  async function handleFileChange(e) {
    const files = Array.from(e.target.files)
    e.target.value = ''
    if (!files.length) return

    setLoading(true)
    try {
      const sessionId = ensureDocumentSession()
      const uploaded = []
      for (const file of files) {
        if (file.type.startsWith('image/')) {
          throw new Error('图片分析暂不进入文档检索；请上传 PDF、Word、TXT 或 CSV 文档')
        }
        const result = await api.uploadDocument(file, sessionId)
        uploaded.push({ name: result.filename, documentId: result.document_id, chunkCount: result.chunk_count })
      }
      setAttachments(prev => [...prev, ...uploaded])
    } catch (err) {
      window.alert(`文档上传失败：${err.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function removeAttachment(i) {
    const attachment = attachments[i]
    if (attachment?.documentId && documentSessionRef.current) {
      try {
        await api.cleanupDocument(documentSessionRef.current, attachment.documentId)
      } catch (err) {
        window.alert(`移除文档失败：${err.message}`)
        return
      }
    }
    setAttachments(prev => prev.filter((_, j) => j !== i))
  }

  function handleSuggest(text) {
    setInput(text)
    setMode('chat')
    inputRef.current?.focus()
  }

  async function send() {
    if (loading) return
    const content = mode === 'chat' ? input.trim() : stockCode.trim()
    if (!content) return

    const asksToExecuteTrade = mode === 'chat' && /(?:帮我|替我|直接|执行|现在).{0,10}(?:买入|卖出|下单|撤单|调仓|交易)/.test(content)
    if (asksToExecuteTrade) {
      const blocked = [...messages,
        { role: 'user', content, type: 'chat' },
        { role: 'assistant', content: '为保护你的账户，coding agent 只提供有证据的投研和回测，不执行买卖、下单、撤单或调仓。你可以改为请求“分析这项交易的风险和依据”。', type: 'chat' },
      ]
      setMessages(blocked)
      setInput('')
      saveConversation(blocked)
      return
    }

    const userMsg = {
      role: 'user',
      content: mode === 'chat' ? content : `分析股票: ${content}${stockName ? ` (${stockName})` : ''}`,
      type: mode,
      attachments: attachments.length > 0 ? attachments.map(item => typeof item === 'string' ? item : item.name) : undefined,
    }
    const newMsgs = [...messages, userMsg]
    setMessages(newMsgs)
    setInput('')
    setAttachments([])
    setLoading(true)

    try {
      let assistantMsg
      if (mode === 'chat') {
        const d = await api.chat(content, model, documentSessionRef.current)
        if (d.intent === 2 && d.decision !== undefined) {
          assistantMsg = { role: 'assistant', content: d, type: 'analyze' }
        } else {
          const reply = d.content || d.reply || d.response || d.answer || (typeof d === 'string' ? d : JSON.stringify(d))
          assistantMsg = {
            role: 'assistant', content: reply, type: 'chat', runId: d.run_id,
            traceSummary: d.trace_summary, publishStatus: d.publish_status,
            publishReasons: d.publish_reasons,
          }
        }
      } else {
        const d = await api.analyze(content, model, documentSessionRef.current)
        assistantMsg = { role: 'assistant', content: d, type: 'analyze' }
      }
      const finalMsgs = [...newMsgs, assistantMsg]
      setMessages(finalMsgs)
      saveConversation(finalMsgs)
    } catch (err) {
      const errMsg = err.message === 'Failed to fetch' || err.message === 'fetch failed'
        ? '服务暂时不可用，请稍后重试'
        : `请求失败：${err.message}`
      const failedMsgs = [...newMsgs, { role: 'assistant', content: errMsg, type: 'chat' }]
      setMessages(failedMsgs)
      saveConversation(failedMsgs)
    } finally {
      setLoading(false)
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="cl-layout">
      {/* ── Sidebar ── */}
      <div className="cl-sidebar">
        {/* Logo */}
        <div className="cl-sidebar-top">
          <button className="cl-logo-btn" onClick={() => navigate('/')}>
            <div className="cl-logo-icon">A</div>
            <span className="cl-logo-text">AlphaStock</span>
          </button>
          <button className="cl-icon-btn cl-return-btn" onClick={() => navigate('/')} title="返回落地页" aria-label="返回落地页">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>
            <span>返回</span>
          </button>
        </div>

        {/* New chat button */}
        <div style={{ padding: '8px 12px' }}>
          <button className="cl-new-chat" onClick={newConversation}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            新对话
          </button>
        </div>

        {conversationError && (
          <div className="cl-conversation-error">
            <span>{conversationError}</span>
            <button onClick={() => setConversationError('')} title="关闭提示">×</button>
          </div>
        )}

        {/* Conversation list */}
        <div className="cl-conv-section">
          {conversations.length > 0 && (
            <div className="cl-section-label">最近</div>
          )}
          <div className="cl-conv-list">
            {conversations.length === 0 ? (
              <div className="cl-conv-empty">暂无历史对话</div>
            ) : conversations.map(conv => {
              const id = conv.id || conv.conv_id
              return (
                <div key={id} className={`cl-conv-item${currentConvId === id ? ' active' : ''}`} onClick={() => loadConversation(conv)}>
                  <span className="cl-conv-title">{conv.title || '未命名对话'}{conv.syncStatus === 'failed' && <small title="尚未同步到服务器"> · 未同步</small>}</span>
                  <button className="cl-conv-del" onClick={e => deleteConversation(e, conv)} title="删除">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  </button>
                </div>
              )
            })}
          </div>
        </div>

        {/* User panel */}
        <div className="cl-user-panel">
          {token ? (
            <>
              <div className="cl-user-avatar">{(username || '?')[0].toUpperCase()}</div>
              <div className="cl-user-info">
                <div className="cl-user-name">{username}</div>
              </div>
              <button className="cl-icon-btn" onClick={logout} title="退出登录" aria-label="退出登录">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
              </button>
            </>
          ) : (
            <div className="cl-logged-out">
              <span className="cl-logged-out-label">未登录</span>
              <button className="cl-new-chat" onClick={() => open('login')}>登录</button>
            </div>
          )}
        </div>
      </div>

      {/* ── Main ── */}
      <div className="cl-main">
        <div className="cl-messages" id="cl-messages-scroll">
          {!token ? (
            <AuthGate />
          ) : messages.length === 0 ? (
            <WelcomeScreen username={username} onSuggest={handleSuggest} />
          ) : (
            <>
              {messages.map((msg, i) => <ChatBubble key={i} msg={msg} />)}
              {loading && (
                <div className="cb-row cb-ai">
                  <div className="cb-ai-avatar">A</div>
                  <div className="cb-ai-text cb-thinking">
                    <span className="cb-dot" /><span className="cb-dot" /><span className="cb-dot" />
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* ── Input ── */}
        {token && (
          <div className="cl-input-area">
            <div className="cl-input-box">
              {/* Mode toggle */}
              <div className="cl-mode-row">
                <button className={`cl-mode-btn${mode === 'chat' ? ' active' : ''}`} onClick={() => setMode('chat')}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
                  普通对话
                </button>
                <button className={`cl-mode-btn${mode === 'analyze' ? ' active' : ''}`} onClick={() => setMode('analyze')}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
                  投研分析
                </button>
              </div>

              {/* Text input */}
              {mode === 'chat' ? (
                <textarea
                  ref={inputRef}
                  className="cl-textarea"
                  placeholder="询问任何投资问题…"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={onKeyDown}
                  rows={1}
                />
              ) : (
                <div className="cl-analyze-row">
                  <input
                    className="cl-analyze-input"
                    placeholder="股票代码，如 600150"
                    value={stockCode}
                    onChange={e => setStockCode(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && send()}
                  />
                  <input
                    className="cl-analyze-input"
                    placeholder="股票名称（可选）"
                    value={stockName}
                    onChange={e => setStockName(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && send()}
                    style={{ flex: 1.4 }}
                  />
                </div>
              )}

              {attachments.length > 0 && (
                <div className="cl-attach-list">
                  {attachments.map((attachment, i) => (
                    <div key={i} className="cl-attach-chip">
                      <span>📎 {typeof attachment === 'string' ? attachment : `${attachment.name}（${attachment.chunkCount} 个检索片段）`}</span>
                      <button onClick={() => removeAttachment(i)}>✕</button>
                    </div>
                  ))}
                </div>
              )}

              {/* Toolbar */}
              <div className="cl-toolbar">
                <div className="cl-toolbar-left">
                  <input type="file" ref={fileInputRef} style={{ display: 'none' }} onChange={handleFileChange} multiple accept="image/*,.pdf,.txt,.csv,.xlsx" />
                  <button className="cl-tool-btn cl-doc-btn" onClick={() => fileInputRef.current?.click()} title="添加证据文档（只读）">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
                    <span>＋ 文档</span>
                  </button>
                  {token && <ApprovalModePanel modeState={approvalMode} onModeChange={changeApprovalMode} onOpenGovernance={() => navigate('/governance')} error={approvalError} />}
                </div>
                <div className="cl-toolbar-right">
                  <select className="cl-model-select" value={model} onChange={e => setModel(e.target.value)}>
                    {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </select>
                  {mode === 'analyze' && <span className="cl-hint">约需 30-60s</span>}
                  <button className={`cl-send-btn${(mode === 'chat' ? input.trim() : stockCode.trim()) && !loading ? ' ready' : ''}`} onClick={send} disabled={loading}>
                    {loading ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spin-icon"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" strokeLinecap="round"/></svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                    )}
                  </button>
                </div>
              </div>
            </div>
            <div className="cl-input-hint">AlphaStock AI 可能会犯错，请核实重要信息</div>
          </div>
        )}
      </div>
    </div>
  )
}
