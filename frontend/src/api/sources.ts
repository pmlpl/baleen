/**
 * 导入相关的接口封装。字段名逐字照 docs/api.md §2,不许改名。
 * 组件里只能调这里的函数,不许自己 fetch(AGENTS.md §9)。
 *
 * ⚠️ 实测偏差(2026-10-29):
 * - `parse_status` 实测会出现 `error`(解析层就是这么报的,见 HANDOFF §1 线 B 的统计口径),
 *   而契约 v1 写的是 `failed`。两个都收,视图都按「解析失败」显示。
 * - 2026-10-31 起 `pending` 是合法值:刚上传、还没轮到 worker 解析的瞬态(0009 迁移),
 *   之前这段窗口一直是 `error` 占位,视图照旧先看 `job.status` 就不受影响。
 * - `message` / `last_error` 允许为 null(契约里也是 null)。
 * 轮询是最频繁的路径,这里的归一化保证 null 不会漏进视图把页面打挂。
 */
import { http } from './http'

/** 单文件上传上限(后端 413 同源) */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

/** job.status 五态 —— docs/api.md §2「前端就按这五个画状态机,别自己发明」 */
export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

/**
 * parse_status:pending 是「上传了还没轮到 worker 解析」的瞬态(0009 迁移起),
 * ok 之外的后四个都是「没拿到正文」。
 */
export type ParseStatus =
  | 'pending'
  | 'ok'
  | 'no_text_expected'
  | 'unsupported_format'
  | 'failed'
  | 'error'

export interface JobInfo {
  id: number
  status: JobStatus
  attempts: number
  last_error: string | null
}

export interface ReviewRun {
  gate: string
  verdict: string
  quality: string | null
  note_type: string | null
  score: number | null
  action: string | null
  reasons: string[]
  model: string | null
  cost_tokens: number
  latency_ms: number
}

export interface SourceDetail {
  id: number
  filename: string
  ext: string
  size_bytes: number
  mime: string
  parse_status: ParseStatus
  message: string | null
  warnings: string[]
  g2_skipped: boolean
  /** 导入时是否跑过 AI 标题结构整理 */
  headings_rewritten: boolean
  note_id: number | null
  created_at: string
  job: JobInfo
  review_runs: ReviewRun[]
}

export interface UploadResponse {
  source_id: number
  job_id: number
  job_status: JobStatus
  deduped: boolean
}

const JOB_STATUSES: readonly JobStatus[] = ['queued', 'running', 'done', 'failed', 'cancelled']
const PARSE_STATUSES: readonly ParseStatus[] = [
  'pending',
  'ok',
  'no_text_expected',
  'unsupported_format',
  'failed',
  'error',
]

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
}

function asText(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNullableText(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function asTextArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === 'string')
    : []
}

/** 认不出来的 job 状态当成 queued(宁可继续轮询,也不要错报成失败) */
function toJobStatus(value: unknown): JobStatus {
  return JOB_STATUSES.find((status) => status === value) ?? 'queued'
}

/** 认不出来的 parse_status 当成 failed(视图会显示「解析失败 + 原因」;pending 已显式收编) */
function toParseStatus(value: unknown): ParseStatus {
  return PARSE_STATUSES.find((status) => status === value) ?? 'failed'
}

function toJobInfo(raw: unknown): JobInfo {
  const job = asRecord(raw)
  return {
    id: asNumber(job.id),
    status: toJobStatus(job.status),
    attempts: asNumber(job.attempts),
    last_error: asNullableText(job.last_error),
  }
}

function toReviewRun(raw: unknown): ReviewRun {
  const run = asRecord(raw)
  return {
    gate: asText(run.gate),
    verdict: asText(run.verdict),
    quality: asNullableText(run.quality),
    note_type: asNullableText(run.note_type),
    score: typeof run.score === 'number' ? run.score : null,
    action: asNullableText(run.action),
    reasons: asTextArray(run.reasons),
    model: asNullableText(run.model),
    cost_tokens: asNumber(run.cost_tokens),
    latency_ms: asNumber(run.latency_ms),
  }
}

function toSourceDetail(raw: unknown): SourceDetail {
  const data = asRecord(raw)
  return {
    id: asNumber(data.id),
    filename: asText(data.filename, '(未知文件)'),
    ext: asText(data.ext),
    size_bytes: asNumber(data.size_bytes),
    mime: asText(data.mime),
    parse_status: toParseStatus(data.parse_status),
    message: asNullableText(data.message),
    warnings: asTextArray(data.warnings),
    g2_skipped: data.g2_skipped === true,
    headings_rewritten: data.headings_rewritten === true,
    note_id: typeof data.note_id === 'number' ? data.note_id : null,
    created_at: asText(data.created_at),
    job: toJobInfo(data.job),
    review_runs: Array.isArray(data.review_runs) ? data.review_runs.map(toReviewRun) : [],
  }
}

function toUploadResponse(raw: unknown): UploadResponse {
  const data = asRecord(raw)
  return {
    source_id: asNumber(data.source_id),
    job_id: asNumber(data.job_id),
    job_status: toJobStatus(data.job_status),
    deduped: data.deduped === true,
  }
}

/** POST /api/sources —— multipart/form-data,单文件 ≤ 50 MB */
export async function createSource(file: File, noLlm = false): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  const query = noLlm ? '?no_llm=true' : ''
  return toUploadResponse(await http.postForm<unknown>(`/sources${query}`, form))
}

/** GET /api/sources/{id} —— 前端轮询这一个接口就能拿到全链路状态 */
export async function getSource(sourceId: number): Promise<SourceDetail> {
  return toSourceDetail(await http.get<unknown>(`/sources/${sourceId}`))
}
