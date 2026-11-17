/**
 * 附件统一解析接口封装:POST /api/parse(multipart,不入库)。
 * 字段名逐字照后端 app/api/parse.py,不许改名。
 */
import { http } from './http'

/** 文内提取的图片(md/html 的 data URI 与本地图):字节转 data URL 回传,前端接进视觉通道。 */
export interface ParseImage {
  name: string
  mime: string
  data_url: string
}

export type ParseStatus = 'ok' | 'no_text_expected' | 'unsupported_format' | 'error'

export interface ParseAttachmentResponse {
  status: ParseStatus
  /** 解析正文;mode=analyzed 时是 AI 分析报告而非原文 */
  text: string
  /** full=全文直塞;analyzed=大文件经 map-reduce 分析提炼(丢细节) */
  mode: 'full' | 'analyzed'
  images: ParseImage[]
  /** 后端提示(编码容错/图片超限/大文件已分析等),原样透传给用户 */
  warnings: string[]
  /** status≠ok 时给用户看的说明(如 .doc 的转换提示) */
  message: string | null
  truncated: boolean
}

function asText(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}

function normalizeImage(raw: unknown): ParseImage | null {
  if (typeof raw !== 'object' || raw === null) return null
  const r = raw as Record<string, unknown>
  if (typeof r.data_url !== 'string' || r.data_url === '') return null
  return {
    name: asText(r.name, '[图片]'),
    mime: asText(r.mime, 'image/png'),
    data_url: r.data_url,
  }
}

/** 防御性归一化(sources.ts 的 toUploadResponse 同款纪律):字段缺了给兜底不炸 */
function normalize(raw: unknown): ParseAttachmentResponse {
  const r = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
  return {
    status: (asText(r.status, 'error') as ParseStatus),
    text: asText(r.text),
    mode: r.mode === 'analyzed' ? 'analyzed' : 'full',
    images: Array.isArray(r.images)
      ? r.images.map(normalizeImage).filter((x): x is ParseImage => x !== null)
      : [],
    warnings: Array.isArray(r.warnings)
      ? r.warnings.filter((x): x is string => typeof x === 'string')
      : [],
    message: typeof r.message === 'string' ? r.message : null,
    truncated: r.truncated === true,
  }
}

/** 📎 文档类附件统一交后端 G0 解析(txt/md/html/docx/pdf/pptx 一套口径,不入库)。
 * 大文件由后端分层:≤40000 字符全文直塞,超出走 map-reduce 分析(mode=analyzed)。 */
export function parseAttachment(file: File): Promise<ParseAttachmentResponse> {
  const form = new FormData()
  form.append('file', file)
  return http.postForm<unknown>('/parse', form).then(normalize)
}
