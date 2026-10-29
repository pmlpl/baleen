/**
 * 问答接口封装(阶段2:聊天式多轮)。
 * POST /api/qa/chat —— session_id 省略自动建会话;GET sessions 列表/详情;DELETE 删会话。
 * 字段名逐字照后端 app/api/qa.py,不许改名。
 */
import { http } from './http'

export interface QaCitation {
  /** 出处编号,对应答案里的 [n] */
  n: number
  note_id: number
  note_title: string
  heading_path: string | null
  snippet: string
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
}

export interface ChatResponse {
  session_id: number
  session_title: string
  user_message: QaMessage
  assistant_message: QaMessage
  /** 实际用于检索的词(追问会先经 LLM 改写,与原话可能不同) */
  search_query: string
  took_ms: number
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
