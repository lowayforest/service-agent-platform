import type { ChatResponse, HealthResponse, KnowledgeSource } from './types'

const API_PREFIX = '/api'

async function readError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail || `请求失败（HTTP ${response.status}）`
  } catch {
    return `请求失败（HTTP ${response.status}）`
  }
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${API_PREFIX}/health`, { signal })
  if (!response.ok) throw new Error(await readError(response))
  return response.json() as Promise<HealthResponse>
}

export async function askKnowledgeBase(
  question: string,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const response = await fetch(`${API_PREFIX}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
    signal,
  })
  if (!response.ok) throw new Error(await readError(response))
  return response.json() as Promise<ChatResponse>
}

const previewSource: KnowledgeSource = {
  id: 'S1',
  chunk_id: 'preview-source',
  source:
    'data/processed/full-corpus/材料二/技术标准等（第二部分）/内河航道公共服务信息发布指南JTS-T+321-2022.pdf.md',
  locator: '交通运输部关于发布《内河航道公共服务信息发布指南》的公告',
  score: 0.7962,
  excerpt:
    '现发布《内河航道公共服务信息发布指南》（以下简称《指南》）。《指南》为水运工程建设推荐性行业标准，标准代码为 JTS/T 321—2022。',
}

export async function askPreview(question: string): Promise<ChatResponse> {
  await new Promise((resolve) => window.setTimeout(resolve, 720))

  if (/实时|今天|当前|现在/.test(question) && /水深|水位|气象|管制/.test(question)) {
    return {
      answer:
        '当前版本尚未接入权威实时数据接口，因此无法确认实时水深、水位、气象或航道管制信息。请以航道管理、海事等权威系统的最新发布为准。',
      sources: [],
      blocked_realtime: true,
    }
  }

  return {
    answer:
      '《内河航道公共服务信息发布指南》的标准编号是 **JTS/T 321—2022**。\n\n根据交通运输部发布的公告，该《指南》为水运工程建设推荐性行业标准，标准代码明确标注为 JTS/T 321—2022 [S1]。\n\n> 当前为本地界面预览数据。连接 RAG API 后，将展示知识库的真实回答。',
    sources: [previewSource],
    blocked_realtime: false,
  }
}
