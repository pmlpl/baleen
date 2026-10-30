/**
 * 问答接口封装(阶段2:聊天式多轮)。
 * POST /api/qa/chat —— session_id 省略自动建会话;GET sessions 列表/详情;DELETE 删会话。
 * 字段名逐字照后端 app/api/qa.py,不许改名。
 */
import { getToken } from '../auth/token'
import { BASE_URL, http } from './http'

export interface QaCitation {
  /** 出处编号,对应答案里的 [n](联网来源为 [W n]) */
  n: number
  /** 库内笔记出处;联网来源为 null(用 url) */
  note_id: number | null
  note_title: string
  heading_path: string | null
  snippet: string
  /** 联网来源的网页链接;库内出处为 null */
  url: string | null
}

export interface QaMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  citations: QaCitation[]
  model: string | null
  cost_tokens: number
  /** prompt 前缀缓存命中率 hit/(hit+miss);null = 该条没有缓存数据(如 user 消息) */
  cache_hit_rate: number | null
}

export interface ChatRequest {
  session_id?: number | null
  question: string
  top_k?: number
  /** 用户引用的笔记 id:告诉 AI「在问什么」;检索仍会跑找关联笔记 */
  note_ids?: number[]
}

export interface ChatResponse {
  session_id: number
  session_title: string
  user_message: QaMessage
  assistant_message: QaMessage
  /** 实际用于检索的词(追问会先经 LLM 改写,与原话可能不同) */
  search_query: string
  /** True = 库内没命中,回答基于联网搜索(citations 带 url) */
  used_web: boolean
  took_ms: number
}

export interface SaveNoteItem {
  note_file: string
  title: string
  organized_chars: number
  import_enqueued: boolean
  import_job_id: string | null
}

export interface SaveNoteResponse {
  note_file: string
  organized_chars: number
  import_enqueued: boolean
  import_job_id: string | null
  /** 多篇模式(split/topics)的完整产物;单篇时长度 1 */
  notes: SaveNoteItem[]
}

/** 存为笔记的选项;不带 = 旧行为(整段一篇) */
export interface SaveNoteRequest {
  mode: 'single' | 'split' | 'topics'
  topic_ids?: number[]
  /** 用户自由输入的补充要求(如「只要 axios 部分」) */
  instruction?: string
}

/** note-plan 返回的一个主题;id 即数组下标 */
export interface NoteTopicView {
  id: number
  title: string
  summary: string
  msg_start: number
  msg_end: number
}

export interface NotePlanResponse {
  topics: NoteTopicView[]
  /** split = 主题间基本无关,建议拆;single = 同一主题 */
  suggestion: 'split' | 'single'
}

/** POST /api/qa/sessions/{id}/note-plan —— 存笔记前的主题分析 */
export function planSessionNote(sessionId: number): Promise<NotePlanResponse> {
  return http.post<NotePlanResponse>(`/qa/sessions/${sessionId}/note-plan`, {})
}

/** POST /api/qa/sessions/{id}/save-note —— 会话整理成 Markdown:落 notes_out/ + no_llm 入库 */
export function saveSessionNote(
  sessionId: number,
  body?: SaveNoteRequest,
): Promise<SaveNoteResponse> {
  return http.post<SaveNoteResponse>(`/qa/sessions/${sessionId}/save-note`, body ?? {})
}

export interface QaSessionItem {
  id: number
  title: string
  created_at: string
}

export interface QaSessionsPage {
  total: number
  items: QaSessionItem[]
}

export interface QaSessionDetail {
  id: number
  title: string
  created_at: string
  messages: QaMessage[]
}

const TOP_K_DEFAULT = 5

/** POST /api/qa/chat —— 提问;sessionId 传 null = 新会话 */
export function sendQuestion(
  sessionId: number | null,
  question: string,
  topK = TOP_K_DEFAULT,
): Promise<ChatResponse> {
  const body: ChatRequest = { question, top_k: topK }
  if (sessionId !== null) body.session_id = sessionId
  return http.post<ChatResponse>('/qa/chat', body)
}

export interface StreamMeta {
  session_id: number
  session_title: string
  search_query: string
  /** 本次回答用的模型(后端从当前配置读) */
  model?: string
  /** 检索/改写耗时(毫秒):用于区分「在检索」还是「在等模型」 */
  prepare_ms?: number
  user_message: QaMessage
}

export interface StreamHandlers {
  onMeta?: (meta: StreamMeta) => void
  onDelta?: (text: string) => void
  onDone?: (data: { assistant_message: QaMessage; took_ms: number }) => void
  onError?: (detail: string) => void
}

/**
 * POST /api/qa/chat/stream —— SSE 流式问答。
 * 浏览器原生 EventSource 不支持 POST,这里用 fetch + ReadableStream 手工解析 SSE 帧
 * (event:/data:),流式读取在 api 层实现(组件仍不许直接 fetch)。
 */
export async function streamChat(
  sessionId: number | null,
  question: string,
  handlers: StreamHandlers,
  topK = TOP_K_DEFAULT,
  signal?: AbortSignal,
  noteIds: number[] = [],
): Promise<void> {
  const body: ChatRequest = { question, top_k: topK, note_ids: noteIds }
  if (sessionId !== null) body.session_id = sessionId
  // 裸 fetch(不是 http 封装):SSE 需要读流,这里手动带上 token
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  if (token !== null) headers.Authorization = `Bearer ${token}`
  const resp = await fetch(`${BASE_URL}/qa/chat/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || resp.body === null) {
    let detail = `请求失败(${resp.status})`
    try {
      const parsed: unknown = await resp.json()
      if (typeof parsed === 'object' && parsed !== null && 'detail' in parsed) {
        detail = String((parsed as { detail: unknown }).detail)
      }
    } catch {
      /* 非 JSON 响应体 → 兜底文案 */
    }
    throw new Error(detail)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const dispatch = (frame: string): void => {
    let event = 'message'
    let data = ''
    for (const line of frame.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7).trim()
      else if (line.startsWith('data: ')) data += line.slice(6)
    }
    if (data === '') return
    const parsed = JSON.parse(data) as Record<string, unknown>
    if (event === 'meta') handlers.onMeta?.(parsed as unknown as StreamMeta)
    else if (event === 'delta') handlers.onDelta?.(String(parsed.text))
    else if (event === 'done')
      handlers.onDone?.(parsed as unknown as { assistant_message: QaMessage; took_ms: number })
    else if (event === 'error') handlers.onError?.(String(parsed.detail ?? '流式回答失败'))
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep = buffer.indexOf('\n\n')
    while (sep !== -1) {
      dispatch(buffer.slice(0, sep))
      buffer = buffer.slice(sep + 2)
      sep = buffer.indexOf('\n\n')
    }
  }
}

/** GET /api/qa/sessions —— 会话列表(最新在前) */
export function listSessions(limit = 50): Promise<QaSessionsPage> {
  return http.get<QaSessionsPage>(`/qa/sessions?limit=${limit}`)
}

/** GET /api/qa/sessions/{id} —— 重开会话(全部消息) */
export function getSession(id: number): Promise<QaSessionDetail> {
  return http.get<QaSessionDetail>(`/qa/sessions/${id}`)
}

/** DELETE /api/qa/sessions/{id} —— 删会话(消息连带删);204 无响应体 */
export function deleteSession(id: number): Promise<null> {
  return http.del<null>(`/qa/sessions/${id}`)
}
