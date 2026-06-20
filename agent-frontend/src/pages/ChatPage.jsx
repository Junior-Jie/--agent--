import { useState, useRef, useEffect } from 'react'
import { Layout, Input, Button, Avatar, Typography, message as antMsg, Tooltip, Tag } from 'antd'
import {
  SendOutlined, DeleteOutlined, RobotOutlined, UserOutlined,
  PlusOutlined, ThunderboltOutlined, MenuFoldOutlined, MenuUnfoldOutlined,
  SearchOutlined, ToolOutlined, BranchesOutlined, SyncOutlined,
} from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { sendMessage } from '../api'
import { useAuth } from '../hooks/useAuth'

const { Header, Sider, Content } = Layout
const { TextArea } = Input
const { Text } = Typography

// 管线步骤（加载时滚动显示）
const STEPS = [
  { icon: <BranchesOutlined />, text: '正在分析意图...' },
  { icon: <SearchOutlined />, text: '正在检索知识库...' },
  { icon: <ToolOutlined />, text: '正在调用工具...' },
  { icon: <SyncOutlined />, text: '正在整理回复...' },
]

// 欢迎页推荐
const PROMPTS = [
  { icon: '忘记密码怎么办？' }, { icon: '帮我看一下有哪些工单？' },
  { icon: '知识库中有哪些登录文档？' }, { icon: '现在几点了？' },
  { icon: '帮我开个工单 数据导出失败 高' }, { icon: 'hello' },
]

function Welcome({ onSend }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '40px 20px', maxWidth: 720, margin: '0 auto' }}>
      <div style={{ textAlign: 'center', marginBottom: 36 }}>
        <Avatar icon={<RobotOutlined />} size={64} style={{ background: 'linear-gradient(135deg, #1677ff, #4096ff)' }} />
        <h1 style={{ fontSize: 24, fontWeight: 600, color: '#1a1a1a', margin: '16px 0 6px' }}>有什么我可以帮你的？</h1>
        <p style={{ color: '#999', fontSize: 14 }}>🔍 RAG 混合检索 · 📋 智能工单 · 🤖 多 Agent 协作</p>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 10, width: '100%' }}>
        {PROMPTS.map((p, i) => (
          <div key={i} onClick={() => onSend(p.icon)} style={{ padding: '12px 16px', border: '1px solid #e8e8e8', borderRadius: 12, cursor: 'pointer', fontSize: 14, color: '#555', background: '#fafafa', transition: 'all .2s' }}
            onMouseEnter={e => { e.target.style.borderColor = '#1677ff'; e.target.style.background = '#f0f5ff' }}
            onMouseLeave={e => { e.target.style.borderColor = '#e8e8e8'; e.target.style.background = '#fafafa' }}>
            {p.icon}
          </div>
        ))}
      </div>
    </div>
  )
}

function PipelineLoader() {
  const [step, setStep] = useState(0)
  useEffect(() => { const t = setInterval(() => setStep(s => (s + 1) % 4), 1500); return () => clearInterval(t) }, [])
  const s = STEPS[step]
  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 20, alignItems: 'flex-start', paddingRight: 60 }}>
      <Avatar size={32} icon={<RobotOutlined />} style={{ background: 'linear-gradient(135deg, #1677ff, #4096ff)', flexShrink: 0 }} />
      <div style={{ padding: '12px 18px', borderRadius: 12, borderTopLeftRadius: 4, background: '#f0f5ff', border: '1px solid #d6e4ff' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          {s.icon} <Text style={{ fontSize: 13, color: '#1677ff', fontWeight: 500 }}>{s.text}</Text>
        </div>
        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
          {STEPS.map((_, i) => (<div key={i} style={{ width: 24, height: 3, borderRadius: 2, background: i === step ? '#1677ff' : i < step ? '#91caff' : '#e8e8e8', transition: 'background .3s' }} />))}
        </div>
      </div>
    </div>
  )
}

// ===== 主页面 =====
export default function ChatPage() {
  const { user, logout } = useAuth()
  const [messages, setMessages] = useState([{ id: 0, role: 'assistant', content: '你好！我是智能助手，有什么可以帮你的？', time: '', meta: null }])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true)
  const [history, setHistory] = useState([])
  const [conversationSid, setConversationSid] = useState(null)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)
  const nextId = useRef(1)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const handleSend = async (txt) => {
    const text = (txt || input).trim()
    if (!text || loading) return

    const userTime = `${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
    const uid = nextId.current; nextId.current += 2

    setMessages(p => [...p,
      { id: uid, role: 'user', content: text, time: userTime },
      { id: uid + 1, role: 'assistant', content: '', time: '', meta: null },
    ])
    setInput('')
    setLoading(true)

    try {
      const res = await sendMessage(text, conversationSid)
      // 第一轮拿到 sid 后，后续请求都带这个 sid → 后端复用同一会话
      if (res.sid) setConversationSid(res.sid)
      const full = res?.reply || ''
      if (!full) throw new Error('empty')
      const intent = res.intent || ''; const trace = res.trace || []

      // 逐字打字效果
      let i = 0
      const timer = setInterval(() => {
        i++; setMessages(p => p.map(m => m.id === uid + 1 ? { ...m, content: full.slice(0, i) } : m))
        if (i >= full.length) {
          clearInterval(timer)
          const endTime = `${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
          setMessages(p => p.map(m => m.id === uid + 1 ? { ...m, content: full, time: endTime, meta: { intent, trace } } : m))
          setLoading(false); inputRef.current?.focus()
        }
      }, 20)

    } catch {
      setMessages(p => p.map(m => m.id === uid + 1 ? { ...m, content: '请求失败，请确认后端已启动' } : m))
      setLoading(false); inputRef.current?.focus()
    }
  }

  const handleKeyDown = e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const newChat = () => {
    const first = messages.find(m => m.role === 'user')
    if (first) setHistory(p => [{ id: Date.now(), title: first.content.slice(0, 25), time: new Date().toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) }, ...p])
    setMessages([{ id: nextId.current++, role: 'assistant', content: '新对话开始了，有什么可以帮你的？', time: '', meta: null }])
    setConversationSid(null); setInput(''); inputRef.current?.focus()
  }

  const renderMsg = (msg) => {
    const isUser = msg.role === 'user'
    return (
      <div key={msg.id} style={{ display: 'flex', gap: 12, marginBottom: 18, flexDirection: isUser ? 'row-reverse' : 'row', paddingLeft: isUser ? 60 : 0, paddingRight: isUser ? 0 : 60 }}>
        <Avatar size={32} icon={isUser ? <UserOutlined /> : <RobotOutlined />} style={{ background: isUser ? '#1677ff' : 'linear-gradient(135deg, #1677ff, #4096ff)', flexShrink: 0, marginTop: 2 }} />
        <div style={{ maxWidth: '75%' }}>
          <div style={{ padding: '12px 18px', borderRadius: 14, borderTopRightRadius: isUser ? 4 : 14, borderTopLeftRadius: isUser ? 14 : 4, background: isUser ? '#e6f4ff' : '#f8f8f8', border: isUser ? '1px solid #bae0ff' : '1px solid #f0f0f0', whiteSpace: 'pre-wrap', lineHeight: 1.8, fontSize: 14, color: '#333', wordBreak: 'break-word' }}>
            {msg.content}{loading && !isUser && msg.id === messages[messages.length - 1]?.id ? '▊' : ''}
          </div>
          {!isUser && msg.meta?.intent && (
            <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
              <Tag color="blue" style={{ fontSize: 11 }}>{msg.meta.intent}</Tag>
              {msg.meta.trace?.map((t, j) => <Tag key={j} style={{ fontSize: 10, color: '#999' }}>{t}</Tag>)}
            </div>
          )}
          {msg.time ? <div style={{ fontSize: 11, color: '#bbb', marginTop: 4, textAlign: isUser ? 'right' : 'left' }}>{msg.time}</div> : null}
        </div>
      </div>
    )
  }

  return (
    <Layout style={{ height: '100vh' }}>
      <Sider width={260} collapsedWidth={0} collapsible collapsed={sidebarCollapsed} onCollapse={setSidebarCollapsed} trigger={null} style={{ background: '#f7f8fa', borderRight: '1px solid #e8e8e8' }}>
        <div style={{ padding: 16 }}>
          <Button type="primary" block icon={<PlusOutlined />} onClick={newChat} style={{ marginBottom: 12, borderRadius: 8 }}>💬 新对话</Button>
          <div style={{ marginBottom: 16, marginTop: 20 }}><Text type="secondary" style={{ fontSize: 11 }}>📝 对话记录</Text></div>
          <div style={{ maxHeight: 'calc(100vh - 160px)', overflow: 'auto' }}>
            {history.map(h => (
              <div key={h.id} style={{ padding: '10px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 13, color: '#555', marginBottom: 4 }} onMouseEnter={e => e.target.style.background = '#f0f0f0'} onMouseLeave={e => e.target.style.background = 'transparent'}>
                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.title}</div>
                <Text type="secondary" style={{ fontSize: 10 }}>{h.time}</Text>
              </div>
            ))}
          </div>
        </div>
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', borderBottom: '1px solid #f0f0f0', padding: '0 20px', display: 'flex', alignItems: 'center', height: 52, gap: 12 }}>
          <Tooltip title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            <Button type="text" icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setSidebarCollapsed(!sidebarCollapsed)} />
          </Tooltip>
          <Text strong style={{ fontSize: 16, color: '#1677ff' }}>🤖 智能客服工单系统</Text>
          <div style={{ flex: 1 }} />
          <Link to="/tickets" style={{ marginRight: 16, fontSize: 13, color: '#555' }}>📋 工单管理</Link>
          {user ? (<><Tag color="green" style={{ borderRadius: 4, fontSize: 11 }}>👤 {user.display_name || user.username}</Tag><Button type="link" size="small" onClick={logout}>退出</Button></>) : (<Link to="/login"><Button type="primary" size="small" ghost style={{ borderRadius: 6 }}>🔑 登录</Button></Link>)}
        </Header>
        <Content style={{ overflow: 'auto', background: '#fff', display: 'flex', flexDirection: 'column' }}>
          {messages.length <= 1 && !loading ? <Welcome onSend={handleSend} /> : (
            <div style={{ maxWidth: 820, width: '100%', margin: '0 auto', padding: '24px 20px', flex: 1 }}>
              {messages.map(renderMsg)}
              {loading && <PipelineLoader />}
              <div ref={bottomRef} />
            </div>
          )}
        </Content>
        <div style={{ borderTop: '1px solid #f0f0f0', padding: '12px 20px 20px', background: '#fff' }}>
          <div style={{ maxWidth: 820, margin: '0 auto', display: 'flex', gap: 10 }}>
            <TextArea ref={inputRef} value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKeyDown} placeholder="输入消息，Enter 发送，Shift+Enter 换行..." autoSize={{ minRows: 1, maxRows: 4 }} disabled={loading}
              style={{ flex: 1, borderRadius: 10, border: '1px solid #d9d9d9', fontSize: 14, padding: '8px 12px' }} />
            <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => handleSend()} loading={loading}
              disabled={!input.trim() && !loading} style={{ borderRadius: 10, height: 40, fontSize: 13, fontWeight: 500 }}>
              {loading ? '处理中...' : '发送 ⚡'}
            </Button>
          </div>
          <div style={{ textAlign: 'center', marginTop: 8, fontSize: 11, color: '#bbb' }}>
            🛡️ 语义守卫 · 🔍 混合检索 · 📋 工单全生命周期{user ? ` · 👤 ${user.display_name || user.username}` : ' · 🚶 游客模式'}
          </div>
        </div>
      </Layout>
    </Layout>
  )
}