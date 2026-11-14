/**
 * 个人中心接口封装。GET /api/profile —— 一次聚合:用户信息 + 使用统计 + 章程预览 + 配置掩码。
 * 纯读;编辑动作走原有端点(章程 → charter.ts,配置 → settings.ts)。
 * settings 块与 GET /api/settings 同口径:密钥只回「是否已设置 + 末 4 位」。
 */
import { http } from './http'

export interface ProfileUser {
  id: number
  username: string
  /** 注册时间,ISO-8601 带时区 */
  created_at: string
}

export interface ProfileStats {
  /** active 笔记数(口径同 GET /api/notes 的 total) */
  notes: number
  /** 导入来源总数 */
  sources: number
  qa_sessions: number
  qa_messages: number
}

export interface ProfileCharter {
  has_charter: boolean
  /** 章程前 300 字(超长带「…」);完整内容走 GET /api/charter */
  preview: string | null
}

export interface ProfileSettings {
  llm_base_url: string
  llm_model: string
  api_key_set: boolean
  api_key_tail: string
  tavily_set: boolean
  tavily_tail: string
}

export interface Profile {
  user: ProfileUser
  stats: ProfileStats
  charter: ProfileCharter
  settings: ProfileSettings
}

export function getProfile(): Promise<Profile> {
  return http.get<Profile>('/profile')
}
