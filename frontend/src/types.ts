export interface KnowledgeSource {
  id: string
  chunk_id: string
  source: string
  locator: string
  score: number
  excerpt: string
}

export interface ChatResponse {
  answer: string
  sources: KnowledgeSource[]
  blocked_realtime: boolean
}

export interface HealthResponse {
  status: string
  chat_backend: string
  chat_model: string
  embedding_backend: string
  embedding_model: string
  indexed_chunks: number
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: KnowledgeSource[]
  blockedRealtime?: boolean
  failed?: boolean
}

export type ServiceState = 'checking' | 'online' | 'preview'
