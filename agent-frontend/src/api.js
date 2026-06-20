import axios from 'axios'

const API_KEY = 'changeme-please-replace-this-secret'
const BACKEND = '/api'   // Vite 代理（开发）/ Nginx 代理（Docker 生产）

const api = axios.create({ baseURL: BACKEND, headers: { 'X-API-Key': API_KEY } })
const getToken = () => localStorage.getItem('token')

// ---- 对话（非流式，axios 直连后端）----
export async function sendMessage(message, sid) {
  const res = await api.post('/chat',
    { message, stream: false, sid: sid || null },
    { headers: { Authorization: `Bearer ${getToken() || ''}` } }
  )
  return res.data
}

// ---- 认证 ----
export async function login(username, password) {
  const res = await api.post('/auth/login', { username, password })
  return res.data
}
export async function register(username, email, password) {
  const res = await api.post('/auth/register', { username, email, password })
  return res.data
}
export async function getMe() {
  const res = await api.get('/auth/me', { headers: { Authorization: `Bearer ${getToken() || ''}` } })
  return res.data
}

// ---- 工单 ----
export async function getTickets(status) {
  const params = status ? { status } : {}
  const res = await api.get('/tickets', { params, headers: { Authorization: `Bearer ${getToken() || ''}` } })
  return res.data
}
export async function createTicketApi(title, priority, description) {
  const res = await api.post('/tickets', { title, priority, description },
    { headers: { Authorization: `Bearer ${getToken() || ''}` } })
  return res.data
}
export async function updateTicketApi(ticketNo, { status, assignee }) {
  const res = await api.put(`/tickets/${ticketNo}`, { status, assignee },
    { headers: { Authorization: `Bearer ${getToken() || ''}` } })
  return res.data
}
export async function deleteTicketApi(ticketNo, hard = false) {
  const params = hard ? '?hard=true' : ''
  const res = await api.delete(`/tickets/${ticketNo}${params}`,
    { headers: { Authorization: `Bearer ${getToken() || ''}` } })
  return res.data
}

// ---- 会话记忆 ----
export async function getConversations() {
  const res = await api.get('/conversations', { headers: { Authorization: `Bearer ${getToken() || ''}` } })
  return res.data
}