/**
 * Token 存储:localStorage,不进 Pinia。
 *
 * 独立成一个无依赖模块,是为了让 http.ts 能读 token 而不引入 Pinia / router
 * (router → charterGate → api/charter → api/http,再加 http → store 就成环了)。
 */

const TOKEN_KEY = 'baleen.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    // 隐私模式下 localStorage 可能抛错,当作没登录
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    // 存不了就算了,当前会话仍能用(内存里的 store 已有 token)
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
  } catch {
    // 同上
  }
}
