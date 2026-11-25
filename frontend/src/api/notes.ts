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

/**
 * DELETE /api/notes/{id} —— **移入纸篓**(软删除,api.md v37)。
 *
 * 只改 status/archived_at:书架与检索立刻看不到,但数据完好、可放回。
 * 满 30 天由后端定时清理真正删除;也可用 purgeNote 立即彻底删。
 */
export async function deleteNote(noteId: number): Promise<void> {
  await http.del(`/notes/${noteId}`)
}

// ---------------- 纸篓(软删除,v37) ----------------

/** 纸篓里的一条。`days_left` 由后端算好(向上取整),前端不自己碰时区。 */
export interface TrashItem {
  id: number
  title: string
  quality: string | null
  source_id: number | null
  chunk_count: number
  /** 移入纸篓的时刻(30 天保留期起点) */
  archived_at: string
  /** 到期自动彻底删除的时刻 */
  expires_at: string
  /** 还剩几天;0 = 今天到期 */
  days_left: number
}

export interface TrashPage {
  total: number
  /** 后端保留期(天),当前 30;前端显示时以此为准,不写死 */
  retention_days: number
  items: TrashItem[]
}

/** GET /api/notes/trash —— 纸篓清单(按移入时间倒序) */
export async function listTrash(limit = 100, offset = 0): Promise<TrashPage> {
  const data = asRecord(await http.get<unknown>(`/notes/trash?limit=${limit}&offset=${offset}`))
  const items = Array.isArray(data.items) ? data.items : []
  return {
    total: asNumber(data.total),
    retention_days: asNumber(data.retention_days, 30),
    items: items.map((raw) => {
      const it = asRecord(raw)
      const maybeNum = (v: unknown): number | null =>
        v === null || v === undefined ? null : asNumber(v)
      return {
        id: asNumber(it.id),
        title: asText(it.title, '(无标题)'),
        quality: it.quality === null || it.quality === undefined ? null : asText(it.quality),
        source_id: maybeNum(it.source_id),
        chunk_count: asNumber(it.chunk_count),
        archived_at: asText(it.archived_at),
        expires_at: asText(it.expires_at),
        days_left: asNumber(it.days_left),
      }
    }),
  }
}

/** POST /api/notes/{id}/restore —— 从纸篓放回书架 */
export async function restoreNote(noteId: number): Promise<NoteListItem> {
  const data = asRecord(await http.post<unknown>(`/notes/${noteId}/restore`, {}))
  return {
    id: asNumber(data.id),
    title: asText(data.title, '(无标题)'),
    lang: asText(data.lang),
    tags: Array.isArray(data.tags) ? (data.tags as string[]) : [],
    quality: data.quality === null || data.quality === undefined ? null : asText(data.quality),
    status: asText(data.status, 'active'),
    source_id:
      data.source_id === null || data.source_id === undefined ? null : asNumber(data.source_id),
    chunk_count: asNumber(data.chunk_count),
    created_at: asText(data.created_at),
  }
}

/** DELETE /api/notes/{id}/purge —— 彻底删除(**不可恢复**:正文/切块/向量/原文件一起清) */
export async function purgeNote(noteId: number): Promise<void> {
  await http.del(`/notes/${noteId}/purge`)
}

