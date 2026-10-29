/**
 * 问答接口封装。POST /api/qa —— 检索 top-k 命中 → LLM 用笔记内容答 + 出处。
 * 字段名逐字照后端 ``app/api/qa.py`` 的 QaResponse。
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

export interface QaRequest {
  question: string
  top_k?: number
}

export interface QaResponse {
  question: string
  answer: string
  citations: QaCitation[]
  took_ms: number
  cost_tokens: number | null
  model: string
}

/** POST /api/qa —— question 1–500 字,top_k 默认 5 上限 20 */
export function askQuestion(question: string, topK = 5): Promise<QaResponse> {
  const body: QaRequest = { question, top_k: topK }
  return http.post<QaResponse>('/qa', body)
}
