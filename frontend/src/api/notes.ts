/**
 * 笔记库接口封装。字段名逐字照 docs/api.md §3,不许改名。
 * (GET /api/sources 三页都用不到,这一批没做前端封装。)
 *
 * ⚠️ 实测偏差(2026-10-29,线 M 的接口已在跑):`GET /api/notes/{id}` 里
 * `markdown` 和 `lang` 真实返回的是 null(契约 v1 写的是 string),`title` 也可能为 null。
 * 所以这里在「网络边界」做一次归一化:TS 类型仍按契约声明,返回值保证不会把 null 漏进视图。
 */
import { http } from './http'

export interface NoteListItem {
  id: number
  title: string
  lang: string
  tags: string[]
  quality: string | null
  status: string
  source_id: number | null
  /** 父块数(section 数),不是子块 */
  chunk_count: number
  created_at: string
}

export interface NoteListResponse {
  total: number
  items: NoteListItem[]
}

export interface NoteParent {
  chunk_id: number
  ord: number
  heading_path: string
  text: string
  token_count: number
}

export interface NoteImage {
  alt: string
  /** 内容寻址 key:有值就走 /api/media/{key} 取原图 */
  media: string | null
  /** 外链或未存字节时的原始引用 */
  src: string | null
}

export interface NoteDetail {
  id: number
  title: string
  lang: string
  tags: string[]
  quality: string | null
  status: string
  source_id: number | null
  markdown: string
  parents: NoteParent[]
  /** 文档顺序的图片清单(v16);前端按 [图片] 标记原位替换 */
  images: NoteImage[]
}

export interface NoteListParams {
  /** 标题模糊匹配(ILIKE),不走向量 */
  q?: string
  limit?: number
  offset?: number
}

/** 上游数据不可信,统一在边界收口(下面几个是 wire → 视图 的收口工具) */
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

function toNoteListItem(raw: unknown): NoteListItem {
  const item = asRecord(raw)
  return {
    id: asNumber(item.id),
    title: asText(item.title, '(无标题)'),
    lang: asText(item.lang),
    tags: asTextArray(item.tags),
    quality: asNullableText(item.quality),
    status: asText(item.status, 'active'),
    source_id: typeof item.source_id === 'number' ? item.source_id : null,
    chunk_count: asNumber(item.chunk_count),
    created_at: asText(item.created_at),
  }
}

function toNoteParent(raw: unknown): NoteParent {
  const parent = asRecord(raw)
  return {
    chunk_id: asNumber(parent.chunk_id),
    ord: asNumber(parent.ord),
    heading_path: asText(parent.heading_path),
    text: asText(parent.text),
    token_count: asNumber(parent.token_count),
  }
}

function toNoteImage(raw: unknown): NoteImage {
  const image = asRecord(raw)
  return {
    alt: asText(image.alt),
    media: asNullableText(image.media),
    src: asNullableText(image.src),
  }
}

function toNoteDetail(raw: unknown): NoteDetail {
  const data = asRecord(raw)
  return {
    id: asNumber(data.id),
    title: asText(data.title, '(无标题)'),
    lang: asText(data.lang),
    tags: asTextArray(data.tags),
    quality: asNullableText(data.quality),
    status: asText(data.status, 'active'),
    source_id: typeof data.source_id === 'number' ? data.source_id : null,
    // 实测为 null → 归一化成空串,视图里再给「没有 markdown 正文」的提示
    markdown: asText(data.markdown),
    parents: Array.isArray(data.parents) ? data.parents.map(toNoteParent) : [],
    images: Array.isArray(data.images) ? data.images.map(toNoteImage) : [],
  }
}

/** GET /api/notes —— 按 created_at DESC */
export async function listNotes(params: NoteListParams = {}): Promise<NoteListResponse> {
  const search = new URLSearchParams()
  if (params.q !== undefined && params.q !== '') search.set('q', params.q)
  search.set('limit', String(params.limit ?? 20))
  search.set('offset', String(params.offset ?? 0))
  const raw = await http.get<unknown>(`/notes?${search.toString()}`)
  const data = asRecord(raw)
  return {
    total: asNumber(data.total),
    items: Array.isArray(data.items) ? data.items.map(toNoteListItem) : [],
  }
}

/** GET /api/notes/{id} —— 404 = 没这条笔记 */
export async function getNote(noteId: number): Promise<NoteDetail> {
  return toNoteDetail(await http.get<unknown>(`/notes/${noteId}`))
}

export interface NoteEditResult {
  id: number
  title: string
  title_changed: boolean
  content_changed: boolean
  chunks_parents: number
  chunks_children: number
  embeddings: number
}

/**
 * PUT /api/notes/{id} —— 用户自编辑;内容编辑会重建切块+向量(同内容 no-op)。
 * 400 = 含图片块的内容编辑(暂不支持,可只改标题);404 = 没这条笔记。
 */
export async function updateNote(
  noteId: number,
  body: { title?: string; markdown?: string },
): Promise<NoteEditResult> {
  const data = asRecord(await http.put<unknown>(`/notes/${noteId}`, body))
  return {
    id: asNumber(data.id),
    title: asText(data.title, '(无标题)'),
    title_changed: data.title_changed === true,
    content_changed: data.content_changed === true,
    chunks_parents: asNumber(data.chunks_parents),
    chunks_children: asNumber(data.chunks_children),
    embeddings: asNumber(data.embeddings),
  }
}

/** DELETE /api/notes/{id} —— 真删除:正文、切块、向量和原文件一起清,不可恢复。404 = 没这条笔记。 */
export async function deleteNote(noteId: number): Promise<void> {
  await http.del(`/notes/${noteId}`)
}
