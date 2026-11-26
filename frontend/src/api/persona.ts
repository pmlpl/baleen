/**
 * 画像接口封装(2026-11-26,由 charter.ts 扩成 persona.ts)。
 *
 * 页面语义从「知识库章程」扩成「我的画像」:左栏问卷事实 + 章程(可原地编辑),
 * 右栏系统统计(每次现算)。
 *
 * 命名说明:后端 ``charter`` 这个词仍在内部沿用(导入流水线 G2 判据、worker 都叫它),
 * 本批只改对外命名,不动内部契约。
 */
import { http } from './http'

export interface PersonaFacts {
  nickname: string
  age_band: string
  gender: string
  education: string
  status: string
  why: string
}

export interface Persona {
  nickname: string | null
  age_band: string | null
  gender: string | null
  education: string | null
  status: string | null
  why: string | null
  has_charter: boolean
  charter: string | null
}

export interface CharterAnswers {
  nickname: string
  age_band: string
  gender: string
  education: string
  status: string
  why: string
}

export interface CharterGenerateResponse {
  charter: string
}

/** 右栏统计。全部是真数出来的聚合,没有估算值。 */
export interface PersonaStats {
  size: { notes: number; chunks: number; sessions: number; asks: number }
  sources: Record<string, number>
  quality: Record<string, number>
  /**
   * 回答方式三分。kb 与 web 有交集(一次回答可同时引笔记和网页),
   * self 独立数「两个都没有」—— 不能用 total - kb - web 算(会负数)。
   */
  answers: { kb: number; web: number; self: number; total: number }
  top_cited: { id: number; title: string; hits: number }[]
  dead_notes: { total: number; items: { id: number; title: string; quality: string }[] }
  /** 纯自答的问题。total 是**真实总数**,items 有 30 条上限(页面折叠,展开才看)。 */
  self_answered: { total: number; items: string[] }
  activity: { week: string; notes: number }[]
  topics: { topic: string; notes: number }[]
  untagged: number
}

export interface AnalyzeTopicsResult {
  tagged: number
  remaining: number
  topics: { topic: string; notes: number }[]
}

export function getPersona(): Promise<Persona> {
  return http.get<Persona>('/persona')
}

/** 保存左栏:None 语义由后端处理(不传=不动、空串=清空),前端整份传当前值。 */
export function savePersona(payload: PersonaFacts & { charter: string }): Promise<Persona> {
  return http.put<Persona>('/persona', payload)
}

export function generateCharter(answers: CharterAnswers): Promise<CharterGenerateResponse> {
  return http.post<CharterGenerateResponse>('/persona/generate', answers)
}

export function getPersonaStats(): Promise<PersonaStats> {
  return http.get<PersonaStats>('/persona/stats')
}

/** 主题打标。retag=true 清空重打(标签集合漂移后收敛用)。 */
export function analyzeTopics(retag = false): Promise<AnalyzeTopicsResult> {
  return http.post<AnalyzeTopicsResult>(`/persona/topics${retag ? '?retag=true' : ''}`, {})
}
