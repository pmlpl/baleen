/**
 * 笔记父块 → HTML(阅读页正文排版)。
 *
 * 从 NoteView.vue 抽出来的纯函数:输入父块纯文本,输出可直接 v-html 的 HTML 片段。
 * 抽出来是为了能脱离 Vue/路由单独跑验证(测试台直接喂真实父块,看分块判定对不对)。
 *
 * 父块是「自然语言 + 裸代码 + 裸图」混排,没有 markdown 围栏,所以不能用
 * renderMarkdown 整套。这里按启发式分块:图片标记 / 表格行 / SVG 块 / mermaid 块 /
 * 代码特征行 / 普通段落。全部先 HTML 转义,原文含 <script> 也只显示成文字。
 */

import { renderMarkdown } from './markdown'

/** 笔记插图(GET /api/notes/{id} 的 images[]),只用到 media/src/alt */
export interface SectionImage {
  media: string | null
  src: string | null
  alt: string
}

/** 跨父块顺序消费图片清单的游标(父块按 ord 顺序渲染,清单也是文档顺序) */
export interface ImageCursor {
  i: number
}

function escapeHtml(raw: string): string {
  return raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/**
 * `[图片] alt` 标记 → 真实插图(chunker 给 image 块产的就是这行文本)。
 * 按文档顺序取下一张图;取完了就保留原标记文字,不假装有图。
 * media key 走 /api/media/,外链走 src 原值(可能加载不出来,给 alt 兜底)。
 */
function makeImageRenderer(images: SectionImage[], cursor: ImageCursor): (line: string) => string | null {
  return (line: string): string | null => {
    if (!/^\s*\[图片\]\s*/.test(line)) return null
    const image = images[cursor.i]
    if (image === undefined) return null
    cursor.i += 1
    const url = image.media !== null ? `/api/media/${image.media}` : (image.src ?? '')
    const alt = escapeHtml(image.alt === '' ? '笔记插图' : image.alt)
    if (url === '') {
      return `<p class="img-missing">[图片] ${alt}(原图未随笔记导入)</p>`
    }
    return (
      `<figure class="note-img"><img src="${escapeHtml(url)}" alt="${alt}" loading="lazy" />` +
      `<figcaption>${alt}</figcaption></figure>`
    )
  }
}

// ---------------- mermaid 块判定 ----------------

// 起始关键字行:出现这些词就开一个图块
const MERMAID_START =
  /^\s*(graph|flowchart|mindmap|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|xychart-beta|quadrantChart|timeline|gitGraph)\b/i
/** 图内关键字(子图/样式/时序/状态…),可带缩进 */
const MERMAID_KEYWORD =
  /^\s*(subgraph|end|style|classDef|class|click|linkStyle|direction|participant|actor|autonumber|note|loop|alt|else|opt|par|and|critical|break|rect|activate|deactivate|over|title|section|dateFormat|axisFormat|todayMarker|excludes|accTitle|accDescr|state)\b/i
/** 连线/关系算子:flowchart(--> -.-> ==>) / sequence(->> -->>) / class(<|-- *--) / ER(||--o{) */
const MERMAID_LINK = /-->|==>|-\.->|~~~|->>|-->>|--[xo]|\|--|--\||<\|--|\*--|o--|\.\.>|\.\.\|/
const mermaidish = (line: string): boolean =>
  MERMAID_START.test(line) ||
  MERMAID_KEYWORD.test(line) ||
  line.trimStart().startsWith('%%') ||
  MERMAID_LINK.test(line) ||
  /^\s*"[^"]*"\s*:/.test(line) || // pie 的 "标签" : 38
  /\w\[.*\]|\w\(.*\)|\w\{.*\}/.test(line) // 节点定义 A[...] B{...}

/**
 * 续收 mermaid 块:行本身像语法,或带缩进(mindmap / flowchart 的裸文本子句)。
 *
 * 旧规则是「中文行 >30 字且不含 ( [ { 才收块」,被引用标记 [W1] 骗过:正文句含方括号
 * 就不命中,整句被吞进图里 → mermaid parse 必败。真实笔记里中招的两处:
 * 「关键点：…[W1]。」「从图中能看清的几个要点：」。改成「顶格即正文 = 收块」,
 * 不再猜长度。
 */
function continuesMermaid(line: string): boolean {
  return mermaidish(line) || /^\s+\S/.test(line)
}

/**
 * 父块里的表格:chunker 把 table 块拍平成「表头行 + 数据行」,单元格用 " | " 连接,
 * 没有 GFM 分隔行(见 backend chunker._table_to_text)。连续同列数的行重建成真表格。
 *
 * 排除条件只留两条,别用完整的 mermaidish 判定:表格单元格里常有 `varchar(512)`
 * 这种写法,会被「节点定义 A(...)」那条正则误伤,把一张表切成两截(实测踩过)。
 * ① 带连线算子的行 —— `A -->|是| B` 也长成「x | y | z」;
 * ② 图声明行 —— graph/flowchart/… 开头。
 *
 * ``allowEmpty``:表格里合并单元格被拍平后,续行首格是空的(` | 新增 | …`)。
 * 表头必须填满,续行允许空格 —— 否则一张表会被切成好几段(实测踩过)。
 */
function tableCells(line: string, allowEmpty = false): string[] | null {
  if (!line.includes(' | ')) return null
  if (MERMAID_START.test(line) || MERMAID_LINK.test(line)) return null
  const cells = line.split('|').map((c) => c.trim())
  if (cells.length < 2) return null
  if (allowEmpty) {
    if (cells.every((c) => c === '')) return null
  } else if (cells.some((c) => c === '')) {
    return null
  }
  return cells
}

/** 表格只能从顶格行开始:缩进行属于 mermaid 的裸文本子句 */
function startsTableRow(line: string): boolean {
  return !/^\s+\S/.test(line) && tableCells(line) !== null
}

/** GFM 分隔行(父块本不该有;万一有就跳过,不能当成数据行) */
function isSeparatorCells(cells: string[]): boolean {
  return cells.every((c) => /^:?-+:?$/.test(c))
}

/** 行组 → 真表格 HTML:补出分隔行,复用 renderMarkdown 的行内格式(粗体/引用徽章) */
function renderTable(rows: string[][], targets: Map<string, string>): string {
  const data = rows.filter((r) => !isSeparatorCells(r))
  const width = (data[0] as string[]).length
  const toRow = (cells: string[]): string => `| ${cells.join(' | ')} |`
  const md = [
    toRow(data[0] as string[]),
    `| ${Array.from({ length: width }, () => '---').join(' | ')} |`,
    ...data.slice(1).map(toRow),
  ].join('\n')
  // renderMarkdown 只把 [Wn] 渲染成静态徽章(它没有出处表可查),这里补成链接
  return `<div class="md">${linkifyTableBadges(renderMarkdown(md), targets)}</div>`
}

// ---------------- 引用徽章 / 链接 ----------------

/** 引用标记:[1] [12] 库内出处,[W1] [W2] 联网来源(与 backend summarizer._CITE_RE 同款) */
const CITE_RE = /(?<![\w.[[])\[(W?\d+)\]/g

/**
 * 出处清单的一条。
 *
 * 原文是 markdown 列表 `- [W1] 标题 — https://…`,但 chunker 拍平 listItem 时
 * 会把 `- ` 前缀吃掉,父块里剩下的是 `[W1] 标题 — https://…`。所以前缀可省。
 * 同时要求行内含地址 —— 正文句子里偶尔以 [1] 开头的不会误判成出处条目。
 */
const APPENDIX_RE = /^(?:[-*]\s*)?\[(W?\d+)\]\s*(.*)$/

/** 出处清单里的地址:http(s) 外链,或站内 /notes/N */
const TARGET_RE = /(https?:\/\/[^\s<>()\]]+|\/notes\/\d+)/

/** 这一行是不是出处条目(编号开头 + 带地址) */
function isAppendixLine(line: string): boolean {
  const m = APPENDIX_RE.exec(line.trim())
  return m !== null && TARGET_RE.test(m[2] as string)
}

/**
 * 从整篇笔记的所有父块里扫出「编号 → 地址」表。
 *
 * 出处清单是整理笔记时写在文末的(见 backend summarizer.append_citation_appendix),
 * 但正文里的 [W1] 可能出现在前面好几个父块里,所以要在渲染前先扫一遍全篇。
 */
export function collectCitationTargets(parentTexts: string[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const text of parentTexts) {
    for (const line of text.split('\n')) {
      if (!isAppendixLine(line)) continue
      const m = APPENDIX_RE.exec(line.trim())
      if (m === null) continue
      const label = m[1] as string
      if (map.has(label)) continue // 同一个编号只认第一条
      const target = TARGET_RE.exec(m[2] as string)
      if (target !== null) map.set(label, target[1] as string)
    }
  }
  return map
}

/** 地址 → 锚点属性;站内路径走路由,外链开新窗口且不带 referrer */
function anchorAttrs(url: string, className: string): string {
  if (url.startsWith('/')) {
    return `class="${className}" href="${escapeHtml(url)}"`
  }
  return `class="${className}" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"`
}

/**
 * 已转义的文本 → 带徽章/链接的 HTML。
 *
 * 只处理三种明确形态,不做任意 URL 猜测:
 * ① 出处清单里的 http(s) 地址 → 外链;
 * ② /notes/N → 站内笔记链接;
 * ③ [W1]/[n] → 有对应地址就渲染成链接,没有就保持徽章(老笔记没有出处清单)。
 *
 * ⚠️ 顺序必须先地址后徽章:徽章替换会吐出 `href="https://…"`,若之后再跑地址替换,
 * 就会把这个 href 里的 URL 再包一层 <a>,整段 HTML 坏掉。
 */
function renderRefs(escaped: string, targets: Map<string, string>): string {
  return escaped
    .replace(TARGET_RE, (url) => `<a ${anchorAttrs(url, 'sec-link')}>${url}</a>`)
    .replace(CITE_RE, (_whole: string, label: string) => {
      const url = targets.get(label)
      const web = label.startsWith('W') ? ' cite-ref--web' : ''
      if (url === undefined) return `<span class="cite-ref${web}">${label}</span>`
      return `<a ${anchorAttrs(url, `cite-ref cite-ref--link${web}`)} title="打开出处 ${label}">${label}</a>`
    })
}

/**
 * 表格里的徽章升级成链接。
 *
 * 表格走 renderMarkdown(renderInline 只认 [Wn] 不认链接语法),所以这里在它输出的
 * HTML 上把 badge span 换成锚点。只匹配自己刚生成的 span,不会误伤别的链接。
 */
function linkifyTableBadges(html: string, targets: Map<string, string>): string {
  if (targets.size === 0) return html
  return html.replace(
    /<span class="cite-ref( cite-ref--web)?">(W?\d+)<\/span>/g,
    (whole, web: string | undefined, label: string) => {
      const url = targets.get(label)
      if (url === undefined) return whole
      const cls = `cite-ref cite-ref--link${web ?? ''}`
      return `<a ${anchorAttrs(url, cls)} title="打开出处 ${label}">${label}</a>`
    },
  )
}

// ---------------- 出处清单 ----------------

/**
 * 一条出处行 → <li>。
 *
 * 形如 `[W1] 标题 — https://…`(列表符号被 chunker 拍平掉了;见
 * backend summarizer.append_citation_appendix)。行内的 [W1] 不渲染成徽章链接 ——
 * 它就是这条自己的编号,旁边已经有地址,再链一遍只是噪音。
 */
function renderAppendixItem(line: string): string {
  const m = APPENDIX_RE.exec(line.trim())
  if (m === null) return ''
  const label = m[1] as string
  let rest = (m[2] as string).trim()
  let url: string | null = null
  const target = TARGET_RE.exec(rest)
  if (target !== null) {
    url = target[1] as string
    rest = rest.replace(url, '').replace(/[—–-]\s*$/, '').trim()
  }
  const web = label.startsWith('W') ? ' cite-ref--web' : ''
  const badge = `<span class="cite-ref${web}">${label}</span>`
  const title = rest === '' ? '' : ` ${escapeHtml(rest)}`
  const link =
    url === null ? '' : ` — <a ${anchorAttrs(url, 'sec-link')}>${escapeHtml(url)}</a>`
  return `<li>${badge}${title}${link}</li>`
}

// ---------------- 普通代码启发式 ----------------

const SVG_START = /^\s*<svg\b/i
const SVG_END = /^\s*<\/svg\s*>\s*$/i
const svgish = (line: string): boolean => line.includes('<') || line.includes('/>') || line.includes('=')

function looksLikeCode(line: string): boolean {
  if (line.trim() === '') return false
  if (/^\s{4,}\S/.test(line)) return true
  if (/^\s*(\/\/|\/\*|\*)/.test(line)) return true
  if (/^\s*[})\]];?\s*$/.test(line)) return true
  if (/[;{]\s*$/.test(line)) return true
  if (/^\s*(function|const|let|var|return|if|for|while|class|import|export|def|async|await)\b/.test(line)) {
    return true
  }
  if (/^\s*[\w.$]+\s*\(.*\)\s*;?\s*$/.test(line) && /[=:]/.test(line)) return true
  return false
}

export function renderSectionText(
  raw: string,
  images: SectionImage[] = [],
  cursor: ImageCursor = { i: 0 },
  targets: Map<string, string> = new Map(),
): string {
  const lines = raw.replace(/\r\n?/g, '\n').split('\n')
  const renderImageMarker = makeImageRenderer(images, cursor)

  const out: string[] = []
  let buffer: string[] = []
  let bufferIsCode: boolean | null = null

  const flush = (): void => {
    if (buffer.length === 0) return
    while (buffer.length > 0 && (buffer[0] as string).trim() === '') buffer.shift()
    while (buffer.length > 0 && (buffer[buffer.length - 1] as string).trim() === '') buffer.pop()
    if (buffer.length === 0) {
      bufferIsCode = null
      return
    }
    if (bufferIsCode === true) {
      out.push(`<pre class="sec-code"><code>${escapeHtml(buffer.join('\n'))}</code></pre>`)
    } else {
      // 段落内单换行保留为 <br>(父块的换行本身有语义:步骤、条件列举)
      out.push(`<p>${buffer.map((l) => renderRefs(escapeHtml(l), targets)).join('<br>')}</p>`)
    }
    buffer = []
    bufferIsCode = null
  }

  for (let i = 0; i < lines.length; i++) {
    const line = (lines[i] as string)!
    // 图片标记优先:原位替换成真实插图
    const imgHtml = renderImageMarker(line)
    if (imgHtml !== null) {
      flush()
      out.push(imgHtml)
      continue
    }
    // 出处清单:连续的「编号 + 地址」行聚成一个列表
    if (isAppendixLine(line)) {
      const items: string[] = []
      let j = i
      while (j < lines.length && isAppendixLine(lines[j] as string)) {
        items.push(renderAppendixItem(lines[j] as string))
        j++
      }
      flush()
      out.push(`<ul class="cite-appendix">${items.join('')}</ul>`)
      i = j - 1
      continue
    }
    // 表格:从顶格行起,连续 ≥2 行、列数一致才认(单行 "a | b" 是普通文字,不误判)
    const firstCells = startsTableRow(line) ? tableCells(line) : null
    if (firstCells !== null) {
      const rows: string[][] = [firstCells]
      let j = i + 1
      while (j < lines.length) {
        const cells = tableCells(lines[j] as string, true)
        if (cells === null || cells.length !== firstCells.length) break
        rows.push(cells)
        j++
      }
      if (rows.length >= 2) {
        flush()
        out.push(renderTable(rows, targets))
        i = j - 1
        continue
      }
    }
    // SVG 优先于普通代码启发式:<svg 开块,到 </svg> 收;没闭合就收到明显中文段落为止
    if (SVG_START.test(line)) {
      flush()
      const block: string[] = [line]
      let j = i + 1
      while (
        j < lines.length &&
        !SVG_END.test(lines[j] as string) &&
        (svgish(lines[j] as string) || (lines[j] as string).trim() === '')
      ) {
        block.push(lines[j] as string)
        j++
      }
      if (j < lines.length && SVG_END.test(lines[j] as string)) {
        block.push(lines[j] as string)
        i = j
      } else {
        i = j - 1 // 没找到闭合:收到哪里算哪里,后续行走普通启发式
      }
      out.push(`<pre><code>${escapeHtml(block.join('\n'))}</code></pre>`)
      continue
    }
    // mermaid 优先于普通代码启发式:起始关键字开块,空行后语法行仍连续则继续收
    if (MERMAID_START.test(line)) {
      flush()
      const block: string[] = [line]
      let j = i + 1
      while (j < lines.length) {
        const cur = lines[j] as string
        if (cur.trim() === '') {
          // 空行:向后看第一个非空行,还是 mermaid 语法就吞掉空行继续,否则收块
          let k = j + 1
          while (k < lines.length && (lines[k] as string)!.trim() === '') k++
          if (k < lines.length && continuesMermaid((lines[k] as string)!)) {
            block.push(cur)
            j++
            continue
          }
          break
        }
        if (!continuesMermaid(cur)) break // 顶格正文 = 图结束,不能吞进块里(否则 parse 必败)
        block.push(cur)
        j++
      }
      i = j - 1
      out.push(`<pre><code>${escapeHtml(block.join('\n'))}</code></pre>`)
      continue
    }
    const isCode = looksLikeCode(line)
    if (bufferIsCode === null) {
      bufferIsCode = isCode
      buffer.push(line)
      continue
    }
    const breaksBlock =
      (bufferIsCode && !isCode && /[\u4e00-\u9fa5]/.test(line) && line.trim().length > 12) ||
      (!bufferIsCode && isCode)
    if (breaksBlock) {
      flush()
      bufferIsCode = isCode
    }
    buffer.push(line)
  }
  flush()
  return out.join('\n')
}
