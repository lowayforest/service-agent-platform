import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { askKnowledgeBase, askPreview, fetchHealth } from './api'
import type {
  ChatMessage,
  HealthResponse,
  KnowledgeSource,
  ServiceState,
} from './types'

const exampleQuestions = [
  '《内河航道公共服务信息发布指南》的标准编号是什么？',
  '航道养护技术规范主要适用于哪些工作？',
  '2024 年长江干线航道运行情况有哪些重点？',
  '今天某航段的实时水深是多少？',
]

const initialMessage: ChatMessage = {
  id: 'welcome',
  role: 'assistant',
  content:
    '您好，我是长江航道公共服务智能助手。您可以向我咨询知识库中的法律法规、技术标准和历史公共服务资料，我会尽量给出可核验的来源。',
}

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    plus: <path d="M12 5v14M5 12h14" />,
    send: <path d="m22 2-7 20-4-9-9-4Zm-11 11L22 2" />,
    copy: <><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></>,
    check: <path d="m20 6-11 11-5-5" />,
    database: <><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12c0 1.7 4 3 9 3s9-1.3 9-3" /></>,
    book: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" /></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h6" /></>,
    shield: <path d="M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V5l8-3 8 3Z" />,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    close: <path d="M18 6 6 18M6 6l12 12" />,
    external: <path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />,
    refresh: <><path d="M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5" /><path d="M4 13a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5" /></>,
  }

  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  )
}

function cleanSourceName(source: string) {
  const filename = source.split('/').pop() || source
  return filename.replace(/\.md$/, '').replace(/\+/g, ' ')
}

function StatusBadge({ state }: { state: ServiceState }) {
  const labels = {
    checking: '正在检查服务',
    online: '知识库服务正常',
    preview: '本地界面预览',
  }
  return (
    <span className={`status-badge status-${state}`}>
      <span className="status-dot" />
      {labels[state]}
    </span>
  )
}

function SourceCard({ source }: { source: KnowledgeSource }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <article className="source-card">
      <button className="source-summary" onClick={() => setExpanded((value) => !value)}>
        <span className="source-id">{source.id}</span>
        <span className="source-heading">
          <strong>{cleanSourceName(source.source)}</strong>
          <small>{source.locator || '文档正文'}</small>
        </span>
        <span className="source-score">{Math.round(source.score * 100)}%</span>
        <span className={`source-chevron ${expanded ? 'expanded' : ''}`}>⌄</span>
      </button>
      {expanded && (
        <div className="source-detail">
          <p>{source.excerpt}</p>
          <span>知识片段：{source.chunk_id.slice(0, 12)}</span>
        </div>
      )}
    </article>
  )
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false)

  async function copyAnswer() {
    await navigator.clipboard.writeText(message.content)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  if (message.role === 'user') {
    return (
      <div className="message-row user-row">
        <div className="user-message">{message.content}</div>
        <div className="avatar user-avatar">您</div>
      </div>
    )
  }

  return (
    <div className="message-row assistant-row">
      <div className="avatar assistant-avatar"><span>江</span></div>
      <div className={`assistant-message ${message.failed ? 'message-failed' : ''}`}>
        {message.blockedRealtime && (
          <div className="boundary-note">
            <Icon name="shield" size={17} />
            已触发实时信息安全边界
          </div>
        )}
        <div className="markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>
        {!!message.sources?.length && (
          <section className="sources-panel">
            <div className="sources-title">
              <Icon name="book" size={17} />
              参考来源 · {message.sources.length}
            </div>
            <div className="sources-list">
              {message.sources.map((source) => (
                <SourceCard key={`${message.id}-${source.id}`} source={source} />
              ))}
            </div>
          </section>
        )}
        {message.id !== 'welcome' && (
          <button className="copy-button" onClick={copyAnswer} title="复制回答">
            <Icon name={copied ? 'check' : 'copy'} size={15} />
            {copied ? '已复制' : '复制'}
          </button>
        )}
      </div>
    </div>
  )
}

function ThinkingMessage() {
  return (
    <div className="message-row assistant-row" aria-label="正在生成回答">
      <div className="avatar assistant-avatar"><span>江</span></div>
      <div className="assistant-message thinking-message">
        <div className="thinking-dots"><span /><span /><span /></div>
        <p>正在检索航道知识库并组织回答…</p>
      </div>
    </div>
  )
}

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([initialMessage])
  const [question, setQuestion] = useState('')
  const [sending, setSending] = useState(false)
  const [serviceState, setServiceState] = useState<ServiceState>('checking')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)

  const checkService = useCallback(async () => {
    setServiceState('checking')
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 2500)
    try {
      const response = await fetchHealth(controller.signal)
      setHealth(response)
      setServiceState('online')
    } catch {
      setHealth(null)
      setServiceState('preview')
    } finally {
      window.clearTimeout(timer)
    }
  }, [])

  useEffect(() => {
    void checkService()
  }, [checkService])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  const hasConversation = useMemo(() => messages.length > 1, [messages.length])

  function resizeTextarea(element: HTMLTextAreaElement) {
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 132)}px`
  }

  function resetConversation() {
    setMessages([initialMessage])
    setQuestion('')
    setSidebarOpen(false)
    window.setTimeout(() => textAreaRef.current?.focus(), 0)
  }

  async function submitQuestion(rawQuestion = question) {
    const normalized = rawQuestion.trim()
    if (!normalized || sending) return

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: normalized,
    }
    setMessages((current) => [...current, userMessage])
    setQuestion('')
    setSending(true)
    setSidebarOpen(false)
    if (textAreaRef.current) textAreaRef.current.style.height = 'auto'

    try {
      const response = serviceState === 'online'
        ? await askKnowledgeBase(normalized)
        : await askPreview(normalized)
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: response.answer,
          sources: response.sources,
          blockedRealtime: response.blocked_realtime,
        },
      ])
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: `暂时无法完成本次问答：${error instanceof Error ? error.message : '服务连接异常'}。请稍后重试。`,
          failed: true,
        },
      ])
      void checkService()
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-block">
          <div className="brand-mark">
            <img src="/brand-logo.png" alt="长江航道公共服务智能助手图标" />
          </div>
          <div>
            <p className="brand-kicker">长江航道</p>
            <h1>公共服务智能助手</h1>
          </div>
          <button className="sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="关闭菜单">
            <Icon name="close" />
          </button>
        </div>

        <button className="new-chat-button" onClick={resetConversation}>
          <Icon name="plus" size={19} />
          开始新咨询
        </button>

        <section className="sidebar-section examples-section">
          <p className="sidebar-label">试试这样问</p>
          <div className="example-list">
            {exampleQuestions.map((item, index) => (
              <button key={item} onClick={() => void submitQuestion(item)} disabled={sending}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                {item}
              </button>
            ))}
          </div>
        </section>

        <section className="knowledge-card">
          <div className="knowledge-icon"><Icon name="database" size={19} /></div>
          <div>
            <span>当前知识库</span>
            <strong>{health?.indexed_chunks?.toLocaleString('zh-CN') || '33,723'} 个知识片段</strong>
            <small>法规 · 标准 · 历史公共服务资料</small>
          </div>
        </section>

        <div className="sidebar-footer">
          <div className="security-line">
            <Icon name="shield" size={16} />
            <span>回答均需依据知识库证据</span>
          </div>
          <p>演示版本 · 数据更新至 2026.09</p>
        </div>
      </aside>

      {sidebarOpen && <button className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} aria-label="关闭菜单" />}

      <main className="main-panel">
        <header className="topbar">
          <button className="menu-button" onClick={() => setSidebarOpen(true)} aria-label="打开菜单">
            <Icon name="menu" />
          </button>
          <div className="topbar-title">
            <strong>智能问答</strong>
            <span>答案可追溯 · 来源可核验</span>
          </div>
          <div className="topbar-actions">
            <StatusBadge state={serviceState} />
            {serviceState === 'preview' && (
              <button className="refresh-button" onClick={() => void checkService()} title="重新检查服务">
                <Icon name="refresh" size={17} />
              </button>
            )}
          </div>
        </header>

        <div className="conversation-area">
          {!hasConversation && (
            <section className="welcome-panel">
              <div className="welcome-eyebrow"><span /> 航道专业知识服务</div>
              <h2>您好，需要了解什么？</h2>
              <p>我可以从已审核的航道法规、技术标准和历史公共服务资料中检索答案，并提供对应出处。</p>
              <div className="capability-grid">
                <div><Icon name="file" /><span><strong>法规与规定</strong><small>查询条款和适用范围</small></span></div>
                <div><Icon name="book" /><span><strong>技术标准</strong><small>定位规范与技术要求</small></span></div>
                <div><Icon name="database" /><span><strong>历史信息</strong><small>核验历年公开资料</small></span></div>
              </div>
            </section>
          )}

          <div className="messages-container">
            {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
            {sending && <ThinkingMessage />}
            <div ref={messagesEndRef} />
          </div>
        </div>

        <footer className="composer-wrap">
          {serviceState === 'preview' && (
            <div className="preview-banner">
              当前使用本地预览回答；连接 RAG API 后会自动切换到真实知识库。
            </div>
          )}
          <div className="composer">
            <textarea
              ref={textAreaRef}
              value={question}
              onChange={(event) => {
                setQuestion(event.target.value.slice(0, 2000))
                resizeTextarea(event.target)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void submitQuestion()
                }
              }}
              placeholder="请输入您想咨询的航道问题…"
              rows={1}
              maxLength={2000}
              disabled={sending}
              aria-label="咨询问题"
            />
            <div className="composer-actions">
              <span>{question.length > 0 ? `${question.length}/2000` : 'Enter 发送 · Shift + Enter 换行'}</span>
              <button
                className="send-button"
                onClick={() => void submitQuestion()}
                disabled={!question.trim() || sending}
                aria-label="发送问题"
              >
                <Icon name="send" size={19} />
              </button>
            </div>
          </div>
          <p className="disclaimer">智能回答可能存在偏差，重要事项请以权威部门发布的信息为准。</p>
        </footer>
      </main>
    </div>
  )
}

export default App
