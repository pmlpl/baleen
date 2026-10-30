/**
 * Charter 闸门:首次使用必须先填问卷才能用其他功能。
 *
 * 单用户无登录体系,所以「首次登录」= 本地首次访问且 DB 里还没有 charter。
 * 状态缓存在模块级(一次会话只查一次),路由守卫据此拦截所有页面。
 *
 * 三个要点:
 * 1. 只查一次 —— checked 后不再打接口,避免每次切路由都请求;
 * 2. 填完即解锁 —— markCharterSaved() 后守卫直接放行,不用重新查;
 * 3. 后端不可用时不锁死 —— 请求失败按「已通过」处理,否则后端挂了用户连页面都进不去
 *    (此时 G2 本来就跑不了,charter.md 文件兜底)。
 */
import { getCharter } from './api/charter'

let checked = false
let hasCharter = false
let pending: Promise<boolean> | null = null

/** 是否已完成问卷(填过 = true)。未检查过时按 false——守卫会先调 ensureCharterChecked。 */
export function charterDone(): boolean {
  return hasCharter
}

/** 问卷已保存:立即解锁,守卫不再拦。 */
export function markCharterSaved(): void {
  hasCharter = true
  checked = true
  pending = null
}

/** 确保 charter 状态已查过;并发调用共享同一次请求。返回 true = 已填过,可放行。 */
export async function ensureCharterChecked(): Promise<boolean> {
  if (checked) return hasCharter
  if (pending === null) {
    pending = getCharter()
      .then((status) => {
        hasCharter = status.has_charter
        return hasCharter
      })
      .catch(() => {
        // 后端不可用:不锁死用户,按已通过处理
        hasCharter = true
        return true
      })
      .finally(() => {
        checked = true
        pending = null
      })
  }
  return pending
}
