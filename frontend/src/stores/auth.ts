/**
 * 认证状态(Pinia setup 风格,与 stores/import.ts 一致)。
 *
 * token 本身存在 auth/token.ts(localStorage),这里只管「当前用户是谁」
 * 以及登录/注册/登出/启动恢复这些动作。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as authApi from '../api/auth'
import type { User } from '../api/auth'
import { clearToken, getToken, setToken } from '../auth/token'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  /** 启动时是否已尝试恢复过登录态(避免路由守卫在恢复完成前就判定未登录) */
  const restored = ref(false)

  const isAuthenticated = computed(() => user.value !== null)
  const username = computed(() => user.value?.username ?? '')

  function apply(result: authApi.AuthResult): void {
    setToken(result.access_token)
    user.value = result.user
    restored.value = true
  }

  async function login(credentials: authApi.Credentials): Promise<void> {
    apply(await authApi.login(credentials))
  }

  async function register(credentials: authApi.Credentials): Promise<void> {
    apply(await authApi.register(credentials))
  }

  function logout(): void {
    clearToken()
    user.value = null
    restored.value = true
  }

  /** 启动时带 token 拉一次 /me;无效 token 直接清掉。 */
  async function restore(): Promise<void> {
    if (restored.value) return
    if (getToken() === null) {
      restored.value = true
      return
    }
    try {
      user.value = await authApi.me()
    } catch {
      // token 过期/无效 → 当作未登录
      clearToken()
      user.value = null
    } finally {
      restored.value = true
    }
  }

  return { user, restored, isAuthenticated, username, login, register, logout, restore }
})
