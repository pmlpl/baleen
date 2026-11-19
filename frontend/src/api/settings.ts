/**
 * 设置接口封装。GET/PUT /api/settings —— 运行时模型配置(热生效,无需重启)。
 * api_key / tavily_api_key 只写不读:GET 只回「是否已设置 + 末 4 位」。
 */
import { http } from './http'

export interface AppSettingsView {
  llm_base_url: string
  llm_model: string
  api_key_set: boolean
  api_key_tail: string
  tavily_set: boolean
  tavily_tail: string
  /** null = 不截断,整篇喂模型 */
  llm_context_tokens: number | null
  /** null = 用 organizer 兜底(131072) */
  llm_max_output_tokens: number | null
  /**
   * 结构图开关(默认 true)。
   * true 也只是「允许」——真正该不该画由后端结构依据闸判定(并列清单≥5项/流程阶段≥3个),
   * 没依据根本不会让模型画;编造出来的图还会被摘掉。
   */
  allow_diagrams: boolean
}

export interface SettingsUpdate {
  /** null/缺省 = 保持不变;空串 = 清空(回退 .env) */
  llm_base_url?: string | null
  llm_api_key?: string | null
  llm_model?: string | null
  tavily_api_key?: string | null
  /** null/缺省 = 保持不变;0 = 清空回退兜底 */
  llm_context_tokens?: number | null
  llm_max_output_tokens?: number | null
  /** 结构图开关:null/缺省 = 保持不变 */
  allow_diagrams?: boolean | null
}

export function getSettings(): Promise<AppSettingsView> {
  return http.get<AppSettingsView>('/settings')
}

export function updateSettings(payload: SettingsUpdate): Promise<AppSettingsView> {
  return http.put<AppSettingsView>('/settings', payload)
}

/** Tavily(联网搜索)key 的掩码视图 */
export interface TavilyStatus {
  tavily_set: boolean
  tavily_tail: string
}
