/**
 * B 站扫码登录接口封装（2026-11-19）。
 * POST /api/bilibili/login —— 弹真浏览器扫码（同步阻塞，最长约 3.5 分钟：180s 等扫码 + 前置导航）。
 * GET /api/bilibili/login —— 当前登录态（是否已配置 + uid + 上次扫码时间）。
 * SESSDATA 凭证只写不读（后端永不回显全文）。
 */
import { http } from './http'

export interface BiliLoginStatus {
  logged_in: boolean
  uid: string
  /** 上次扫码时间，ISO-8601 带时区；未登录时为 null */
  logged_at: string | null
}

export function getBiliLoginStatus(): Promise<BiliLoginStatus> {
  return http.get<BiliLoginStatus>('/bilibili/login')
}

export function startBiliLogin(): Promise<BiliLoginStatus> {
  return http.post<BiliLoginStatus>('/bilibili/login', {})
}
