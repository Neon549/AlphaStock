const BASE = '/api/v1'
const DEFAULT_TIMEOUT_MS = 20000

function headers() {
  const token = sessionStorage.getItem('token')
  return {
    'Content-Type': 'application/json',
    ...(token ? { 'X-Auth-Token': token, 'Authorization': `Bearer ${token}` } : {}),
  }
}

function withToken(body) {
  const token = sessionStorage.getItem('token')
  return token ? { ...body, token } : body
}

async function request(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(BASE + path, { ...options, signal: controller.signal })
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请稍后重试')
    throw error
  } finally {
    clearTimeout(timer)
  }
}

async function post(path, body, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const r = await request(path, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
  }, timeoutMs)
  const text = await r.text()
  let d = {}
  try { d = JSON.parse(text) } catch { d = { detail: text || `请求失败 (${r.status})` } }
  if (!r.ok) throw new Error(d.detail || `请求失败 (${r.status})`)
  return d
}

async function put(path, body) {
  const r = await request(path, {
    method: 'PUT',
    headers: headers(),
    body: JSON.stringify(body),
  })
  const text = await r.text()
  let d = {}
  try { d = JSON.parse(text) } catch { d = { detail: text || `请求失败 (${r.status})` } }
  if (!r.ok) throw new Error(d.detail || `请求失败 (${r.status})`)
  return d
}

async function get(path) {
  const r = await request(path, { headers: headers() })
  const text = await r.text()
  let d = {}
  try { d = JSON.parse(text) } catch { d = { detail: text || `请求失败 (${r.status})` } }
  if (!r.ok) throw new Error(d.detail || `请求失败 (${r.status})`)
  return d
}

async function del(path) {
  const r = await request(path, { method: 'DELETE', headers: headers() })
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail || `请求失败 (${r.status})`)
  }
  return true
}

async function uploadDocument(file, sessionId) {
  const form = new FormData()
  form.append('file', file)
  form.append('session_id', sessionId)
  const token = sessionStorage.getItem('token')
  const r = await request('/upload/document', {
    method: 'POST',
    headers: token ? { 'X-Auth-Token': token, 'Authorization': `Bearer ${token}` } : {},
    body: form,
  }, 120000)
  const text = await r.text()
  let d = {}
  try { d = JSON.parse(text) } catch { d = { detail: text || `请求失败 (${r.status})` } }
  if (!r.ok) throw new Error(d.detail || `上传失败 (${r.status})`)
  return d
}

export const api = {
  login: (username, password) => post('/auth/login', { username, password }),
  register: (username, password, email) => post('/auth/register', { username, password, email: email || '' }),
  verify: (token) => post('/auth/verify', { token }),
  googleToken: (access_token) => post('/auth/google/token', { access_token }, 15000),
  forgotPassword: (email) => post('/auth/forgot-password', { email }),
  resetPassword: (token, new_password) => post('/auth/reset-password', { token, new_password }),

  getApprovalMode: () => get('/memory/approval-mode'),
  setApprovalMode: (payload) => put('/memory/approval-mode', payload),
  getMemoryCandidates: (status = 'pending') => get(`/memory/candidates?status=${encodeURIComponent(status)}`),
  batchMemoryDecision: (candidate_ids, approved, review_note = '') =>
    post('/memory/candidates/batch-decision', { candidate_ids, approved, review_note }),

  chat: (message, model, session_id) => post('/chat', withToken({ message, model, session_id }), 180000),
  analyze: (stock_code, model, session_id) => post('/analyze', withToken({ stock_code, model, session_id }), 180000),
  uploadDocument,
  cleanupDocumentSession: (sessionId) => del(`/upload/session/${encodeURIComponent(sessionId)}`),
  cleanupDocument: (sessionId, documentId) => del(`/upload/session/${encodeURIComponent(sessionId)}/document/${encodeURIComponent(documentId)}`),

  backtest: (params) => post('/backtest', withToken(params)),
  getStrategies: () => get('/backtest/strategies'),
  getSectors: () => get('/backtest/sectors'),
  filterSector: (params) => post('/backtest/filter', withToken(params)),

  scanToday: (params) => post('/scan/today', withToken(params)),

  alphaSingle: (stock_code, stock_name) =>
    post('/alpha/single', withToken({ stock_code, stock_name })),

  getConversations: (username) => get(`/conversations/${encodeURIComponent(username)}`),
  saveConversation: (data) => post('/conversations/save', withToken(data)),
  deleteConversation: (conv_id) => del(`/conversations/${encodeURIComponent(conv_id)}`),
  getRunDiagnostics: (run_id) => get(`/runs/${encodeURIComponent(run_id)}`),
}
