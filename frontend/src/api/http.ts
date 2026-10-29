/** 统一 fetch 封装:组件里不许直接 fetch(AGENTS.md §9),一律从这里走。 */

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api'

/** 少数不带 /api 前缀的端点:docs/api.md §1,/healthz 是历史原因,别改 */
const OUTSIDE_PREFIX: readonly string[] = ['/healthz']

/** 网络层失败(连不上后端)时用的伪状态码,方便统一走 describeApiError */
const NETWORK_STATUS = 0

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

function resolveUrl(path: string): string {
  return OUTSIDE_PREFIX.includes(path) ? path : `${BASE_URL}${path}`
}

/**
 * 从错误响应体里取 detail(docs/api.md:错误一律 {"detail": "<中文>"})。
 * FastAPI 的 422 会把 detail 变成数组,这里一并压成一行文字。
 */
async function readDetail(response: Response): Promise<string | null> {
  try {
    const text = await response.text()
    if (text.length === 0) return null
    const parsed: unknown = JSON.parse(text)
    if (typeof parsed !== 'object' || parsed === null || !('detail' in parsed)) return null
    const detail: unknown = (parsed as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const parts = detail.map((entry: unknown) => {
        if (typeof entry === 'object' && entry !== null && 'msg' in entry) {
          return String((entry as { msg: unknown }).msg)
        }
        return String(entry)
      })
      return parts.length > 0 ? parts.join(';') : null
    }
    return null
  } catch {
    // 响应体不是 JSON(例如 Vite 代理自己报的 500) → 交给调用方的兜底文案
    return null
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await readDetail(response)
    throw new ApiError(
      response.status,
      detail ?? `请求失败(${response.status}:${response.statusText})`,
    )
  }
  return (await response.json()) as T
}

async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(resolveUrl(path), init)
  } catch {
    // fetch 只在真正连不上时抛(TypeError):后端没起 / 代理没通
    throw new ApiError(NETWORK_STATUS, '连不上后端:网络请求失败(确认后端已启动)')
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { headers, body, method = 'GET' } = options
  const response = await send(path, {
    method,
    headers: { Accept: 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return parseResponse<T>(response)
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
  /**
   * multipart/form-data 上传。
   * ⚠️ 故意不设 Content-Type:必须让浏览器自己写 boundary,手写会缺 boundary → 后端 422。
   */
  async postForm<T>(path: string, form: FormData): Promise<T> {
    const response = await send(path, {
      method: 'POST',
      headers: { Accept: 'application/json' },
      body: form,
    })
    return parseResponse<T>(response)
  },
  /** HTTP DELETE。204 No Content 没有响应体,返回 null;其余 2xx 照常解析 JSON。 */
  async del<T>(path: string): Promise<T | null> {
    const response = await send(path, { method: 'DELETE', headers: { Accept: 'application/json' } })
    if (response.status === 204) return null
    return parseResponse<T>(response)
  },
}

/**
 * 把任意错误翻成人话。
 * 4 个待实现接口(线 M)此刻可能还 404 —— 这种情况必须给可读提示,不能让页面白屏。
 */
export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === NETWORK_STATUS) return error.message
    if (error.status === 404) return `后端接口还没就绪(404):${error.message}`
    if (error.status === 413) return '文件超过 50 MB,后端不接受(413)'
    if (error.status === 415) return `格式不支持(415):${error.message}`
    if (error.status === 422) return `请求参数有问题(422):${error.message}`
    // 500/502/503 在 dev 期最常见的原因是「后端没起」(Vite 代理自己回 500),
    // 这里不要回显 "请求失败(500:Internal Server Error)" 这种没用的技术串。
    if (error.status >= 500) return `后端没正确响应(${error.status});先确认后端是否已启动`
    return `请求失败(${error.status}):${error.message}`
  }
  if (error instanceof Error) return error.message
  return String(error)
}
