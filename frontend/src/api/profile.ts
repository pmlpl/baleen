/**
 * 个人中心接口封装。GET /api/profile —— 只回两项:账号身份 + 是否已填章程。
 *
 * **v38 收缩**：原四块聚合里的 `settings`(密钥掩码)、`stats`(使用计数)、
 * `charter.preview`(章程前 300 字)都是别处数据的第二份拷贝,按主人
 * 2026-11-25「不重叠功能和内容」铁律全部删除。
 *
 * 要配置 → `GET /api/settings`(settings.ts)
 * 要完整章程 → `GET /api/charter`(charter.ts)
 */
import { http } from './http'

export interface ProfileUser {
  id: number
  username: string
  /** 注册时间,ISO-8601 带时区 */
  created_at: string
}

export interface ProfileCharter {
  /** 只回「填没填」;完整内容走 GET /api/charter */
  has_charter: boolean
}

export interface Profile {
  user: ProfileUser
  charter: ProfileCharter
}

export function getProfile(): Promise<Profile> {
  return http.get<Profile>('/profile')
}
