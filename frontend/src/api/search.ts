/**
 * 混合检索接口封装。字段名逐字照 docs/api.md §4,不许改名。
 * 命中单位是父块(small-to-big:小块检索、父块出)。
 */
import { http } from './http'

export interface SearchRequest {
  query: string
  top_k?: number
}

export interface SearchHit {
  parent_id: number
  parent_text: string
  heading_path: string
  note_id: number
  note_title: string
  /** RRF 分:Σ 1/(60+名次),没有绝对意义,只用来排相对高低 */
  rrf_score: number
  child_id: number
  /** 命中的那个子块(检索用的中间产物,详情页不展示) */
  child_text: string
  /** 为 null 表示这一路没召回到这个块 */
  vector_rank: number | null
  lexical_rank: number | null
}

export interface SearchResponse {
  query: string
  took_ms: number
  /** 空数组是正常结果(库里没相关内容),不是错误 */
  hits: SearchHit[]
}

/** POST /api/search —— query 1–500 字,top_k 默认 5 上限 20 */
export function searchNotes(query: string, topK = 5): Promise<SearchResponse> {
  const body: SearchRequest = { query, top_k: topK }
  return http.post<SearchResponse>('/search', body)
}
