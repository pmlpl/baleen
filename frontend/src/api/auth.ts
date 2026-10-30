/** 认证接口:注册 / 登录 / 当前用户。 */
import { http } from './http'

export interface User {
  id: number
  username: string
}

export interface AuthResult {
  access_token: string
  token_type: string
  user: User
}

export interface Credentials {
  username: string
  password: string
}

export function register(credentials: Credentials): Promise<AuthResult> {
  return http.post<AuthResult>('/auth/register', credentials)
}

export function login(credentials: Credentials): Promise<AuthResult> {
  return http.post<AuthResult>('/auth/login', credentials)
}

export function me(): Promise<User> {
  return http.get<User>('/auth/me')
}
