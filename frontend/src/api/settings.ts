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
  douyin_set: boolean
  douyin_tail: string
}

export interface SettingsUpdate {
  /** null/缺省 = 保持不变;空串 = 清空(回退 .env) */
  llm_base_url?: string | null
  llm_api_key?: string | null
  llm_model?: string | null
  tavily_api_key?: string | null
  douyin_cookies?: string | null
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
