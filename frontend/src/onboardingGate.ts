/**
 * 上手闸门:登录后的两段必做步骤,顺序固定。
 *
 * 1. **配 API Key** —— 没配就不能用 AI 功能(导入的 G2、问答、章程生成都靠它)。
 * 2. **填章程** —— 决定导入时哪些笔记收、哪些不收。
 *
 * 状态缓存在模块级(一次会话只查一遍),路由守卫据此引导用户。
 * 换账号/退出登录必须调 resetGate(),否则会拿上一个用户的判定结果。
 *
 * 后端不可用时 fail-open(按已通过处理):否则后端一挂,用户连页面都进不去。
 */

import { getCharter } from './api/charter'
import { getSettings } from './api/settings'

export interface GateState {
  /** 该账号是否配了自己的 API Key */
  hasKey: boolean
  /** 该账号是否填过章程 */
  hasCharter: boolean
}

let checked = false
let state: GateState = { hasKey: false, hasCharter: false }
let pending: Promise<GateState> | null = null

/** 同步读当前状态(未检查过时两个都 false)。 */
export function gateState(): GateState {
  return state
}

/** 两步都完成 = 可以正常使用。 */
export function onboardingDone(): boolean {
  return state.hasKey && state.hasCharter
}

/** 章程已保存:立即标记,不用重新查。 */
export function markCharterSaved(): void {
  state = { ...state, hasCharter: true }
  checked = true
  pending = null
}

/** 配置已保存:重新标记(设置页保存后调用)。 */
export function markKeyConfigured(hasKey: boolean): void {
  state = { ...state, hasKey }
}

/** 退出登录 / 切换账号必须调用:否则会沿用上一个用户的上手状态。 */
export function resetGate(): void {
  checked = false
  state = { hasKey: false, hasCharter: false }
  pending = null
}

/** 确保状态已查过;并发调用共享同一次请求。 */
export async function ensureGateChecked(): Promise<GateState> {
  if (checked) return state
  if (pending === null) {
    pending = Promise.all([getSettings(), getCharter()])
      .then(([settings, charter]) => {
        state = { hasKey: settings.api_key_set, hasCharter: charter.has_charter }
        return state
      })
      .catch(() => {
        // 后端不可用:不锁死用户(此时 AI 功能本来也用不了)
        state = { hasKey: true, hasCharter: true }
        return state
      })
      .finally(() => {
        checked = true
        pending = null
      })
  }
  return pending
}
