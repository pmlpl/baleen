/** 统一 fetch 封装:组件里不许直接 fetch(AGENTS.md §9),一律从这里走。 */

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

interface RequestOptions {
  method?: string
  headers?: Record<string, string>
  body?: unknown
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { headers, body, method = 'GET' } = options
  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: { Accept: 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    throw new ApiError(response.status, `请求失败(${response.status}):${response.statusText}`)
  }
  return (await response.json()) as T
}

export const http = {
  get<T>(path: string): Promise<T> {
    return request<T>(path)
  },
  post<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })
  },
}
