/**
 * 极简 markdown 渲染器(手写 —— 表格/嵌套列表也是手写;mermaid 走独立懒加载模块,
 * 数学公式走 math.ts 的 KaTeX)。
 * 支持:标题/段落/有序无序列表(一级嵌套)/表格/引用/代码块/行内 code 粗体 斜体/水平线/引用标记/行内 $...$ 与块级 $$...$$ 公式。
 *
 * 安全:所有原文先 HTML 转义再拼标记,即便原文含 <script> 也只显示成文字;
 * 公式片段在转义前占位隔离,交给 KaTeX 输出安全 HTML。
 * NoteView(笔记正文)与 QaView(问答答案)共用。
 */

import { renderTex, splitMath, type MathSpan } from './math'

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 块级公式 → 居中展示块(渲染失败时 renderTex 内部已退化,不抛错) */
function mathBlockHtml(tex: string): string {
  return `<div class="math-block">${renderTex(tex, true)}</div>`
}

/**
 * 从 lines[i] 起收集一个 $$...$$ 块级公式(首行 $$ 开头,直到某行以 $$ 结尾)。
 * 找不到闭合返回 null —— 不吞行,原行落回普通段落逻辑。
 */
function takeBlockMath(lines: string[], i: number): { html: string; nextIndex: number } | null {
  const first = lines[i]!.trim()
  const single = /^\$\$(.+)\$\$$/.exec(first)
  if (single !== null && single[1]!.trim() !== '') {
    return { html: mathBlockHtml(single[1]!.trim()), nextIndex: i }
  }
  // $$x$$ 后面还跟了别的(如 LLM 写的 `$$..$$ [W4]`)——不是独占块,交给行内扫描器:
  // 它会把行内的 $$..$$ 配成 display 公式,尾巴当普通文字
  if (first.length > 4 && first.endsWith('$$')) return null
  if (first.indexOf('$$', 2) !== -1) return null

  const buf: string[] = []
  if (first.length > 2) buf.push(first.slice(2))
  let j = i + 1
  while (j < lines.length) {
    const t = lines[j]!.trim()
    if (t.endsWith('$$')) {
      const inner = t.slice(0, -2)
      if (inner.trim() !== '') buf.push(inner)
      return { html: mathBlockHtml(buf.join('\n').trim()), nextIndex: j }
    }
    // 真块不会跨越空行/标题/又一个开头;跨过去说明上一块没闭合,不吞行
    // (否则会把后文的标题、正文扫进一个烂公式,KaTeX 报错渲染成一片红)
    if (t === '' || /^#{1,6}\s/.test(t) || t.startsWith('$$')) return null
    buf.push(t)
    j += 1
  }
  return null
}

function renderInline(raw: string): string {
  // 先切出行内代码,受保护不参与后续替换(否则 `arr[1]` 里的 [1] 会被当成引用)。
  // 注意在原文级切(而非转义后),体内在建占位时单独转义。
  const codes: string[] = []
  let working = raw.replace(/`([^`]+)`/g, (_m, body: string) => {
    codes.push(`<code>${escapeHtml(body)}</code>`)
    return `\u0000CODE${codes.length - 1}\u0000`
  })

  // 再切数学片段(原文级 —— KaTeX 要原生反斜杠),占位保护:
  // 公式里的 [1] ** \| 不会被下面的粗体/引用替换吃掉
  const math: string[] = []
  working = splitMath(working)
    .map((part: string | MathSpan) => {
      if (typeof part === 'string') return part
      math.push(renderTex(part.tex, part.display))
      return `\u0000MATH${math.length - 1}\u0000`
    })
    .join('')

  const escaped = escapeHtml(working)

  const withRefs = escaped
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    // 引用标记:[1] [12] 库内出处,[W1] [W2] 联网来源 → 上标小徽章。
    // 前置断言排除 arr[1] 这类数组/索引访问(前面紧挨字母数字下划线或点)。
    .replace(
      /(?<![\w.[])\[(W?\d+)\]/g,
      (_m, ref: string) =>
        ref.startsWith('W')
          ? `<span class="cite-ref cite-ref--web">${ref}</span>`
          : `<span class="cite-ref">${ref}</span>`,
    )

  // 还原行内代码与公式
  return withRefs
    .replace(/\u0000CODE(\d+)\u0000/g, (_m, i: string) => codes[Number(i)] ?? '')
    .replace(/\u0000MATH(\d+)\u0000/g, (_m, i: string) => math[Number(i)] ?? '')
}

// ---------------- 表格 ----------------

/** 表格行:以 | 开头且含 |(裸文本里偶见的 a|b 不算) */
function isTableRow(line: string): boolean {
  return /^\s*\|.*\|\s*$/.test(line)
}

/** 拆一行成单元格;不处理 \| 转义(LLM 输出里极罕见) */
function splitRow(line: string): string[] {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|')) s = s.slice(0, -1)
  return s.split('|').map((c) => c.trim())
}

/** 分隔行:每个单元格都是 :--- / ---: / --- 形态 */
function isSeparatorRow(cells: string[]): boolean {
  return cells.length > 0 && cells.every((c) => /^:?-+:?$/.test(c))
}

/** 分隔行 → 列对齐(居中/右/默认左) */
function alignsOf(separatorCells: string[]): string[] {
  return separatorCells.map((c) => {
    if (c.startsWith(':') && c.endsWith(':')) return 'center'
    if (c.endsWith(':')) return 'right'
    return 'left'
  })
}

function buildTable(header: string[], aligns: string[], bodyRows: string[][]): string {
  const th = (cells: string[]): string =>
    `<tr>${cells.map((c, i) => `<th style="text-align:${aligns[i] ?? 'left'}">${renderInline(c)}</th>`).join('')}</tr>`
  const td = (cells: string[]): string =>
    `<tr>${cells.map((c, i) => `<td style="text-align:${aligns[i] ?? 'left'}">${renderInline(c)}</td>`).join('')}</tr>`
  const body = bodyRows.map(td).join('')
  return `<table><thead>${th(header)}</thead><tbody>${body}</tbody></table>`
}

// ---------------- 列表(支持一级嵌套) ----------------

interface ListFrame {
  tag: 'ul' | 'ol'
  liOpen: boolean
}

export function renderMarkdown(markdown: string): string {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []
  const listStack: ListFrame[] = []
  let inCode = false
  let codeLines: string[] = []
  let paragraph: string[] = []

  const flushParagraph = (): void => {
    if (paragraph.length === 0) return
    out.push(`<p>${renderInline(paragraph.join(' '))}</p>`)
    paragraph = []
  }

  const closeLi = (): void => {
    const top = listStack[listStack.length - 1]
    if (top !== undefined && top.liOpen) {
      out.push('</li>')
      top.liOpen = false
    }
  }

  const closeOneList = (): void => {
    closeLi()
    const top = listStack.pop()
    if (top !== undefined) out.push(`</${top.tag}>`)
  }

  const closeAllLists = (): void => {
    while (listStack.length > 0) closeOneList()
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] as string

    if (line.trimStart().startsWith('```')) {
      if (inCode) {
        out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = []
        inCode = false
      } else {
        flushParagraph()
        closeAllLists()
        inCode = true
      }
      continue
    }

    if (inCode) {
      codeLines.push(line)
      continue
    }

    if (line.trim() === '') {
      flushParagraph()
      closeAllLists()
      continue
    }

    // ---- 块级数学:$$ 独占(收集到闭合行,渲染成居中展示块;没闭合不吞行) ----
    if (line.trimStart().startsWith('$$')) {
      const block = takeBlockMath(lines, i)
      if (block !== null) {
        flushParagraph()
        closeAllLists()
        out.push(block.html)
        i = block.nextIndex // for 的 i++ 会把它指回闭合行的下一行
        continue
      }
    }

    // ---- 表格:本行是表格行 且 下一行是分隔行 ----
    if (i + 1 < lines.length && isTableRow(line) && isSeparatorRow(splitRow(lines[i + 1] as string))) {
      flushParagraph()
      closeAllLists()
      const separator = splitRow(lines[i + 1] as string)
      const aligns = alignsOf(separator)
      const header = splitRow(line)
      const bodyRows: string[][] = []
      let j = i + 2
      while (j < lines.length && isTableRow(lines[j] as string)) {
        bodyRows.push(splitRow(lines[j] as string))
        j += 1
      }
      out.push(buildTable(header, aligns, bodyRows))
      i = j - 1 // for 的 i++ 会把它指回第一行非表格行
      continue
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading !== null) {
      flushParagraph()
      closeAllLists()
      const level = heading[1].length
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
      continue
    }

    if (/^\s*([-*_])\1{2}\s*$/.test(line)) {
      flushParagraph()
      closeAllLists()
      out.push('<hr />')
      continue
    }

    // ---- 列表项(支持一级嵌套:缩进 ≥2 空格 → 上一 <li> 里开子列表) ----
    const indent = line.length - line.trimStart().length
    const ordered = /^\s*(\d+)\.\s+(.*)$/.exec(line)
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line)
    const listItem = ordered ?? bullet
    if (listItem !== null) {
      flushParagraph()
      const tag = ordered === null ? 'ul' : 'ol'
      // 有序内容组在 [2](编号占 [1]),无序仍在 [1]——3b41e7a 加编号捕获组时把三处
      // listItem[1] 一刀切成 [2],无序列表项从此渲染即崩(2026-11-25「历史对话点不开」
      // 根因:任一 "- " 项就让 QaView 整条渲染 TypeError,空白页)
      const itemText = ordered !== null ? ordered[2] : bullet?.[1] ?? ''
      // 有序列表开新 <ol> 时带上首项编号:段落会打断列表,后一段 3. 开头的新列表
      // 不带 start 会从 1 重数(8093 倒金字塔「先 1、2 又 1、2」实锤)
      const listAttr =
        ordered !== null && ordered[1] !== '1' ? ` start="${ordered[1]}"` : ''
      const top = listStack[listStack.length - 1]
      const nested = indent >= 2 && listStack.length === 1
      if (top === undefined) {
        out.push(`<${tag}${listAttr}>`)
        listStack.push({ tag, liOpen: true })
      } else if (nested) {
        // 关键:父 <li> 保持开放(不 closeLi),子列表才能嵌在它内部
        out.push(`<${tag}${listAttr}>`)
        listStack.push({ tag, liOpen: true })
      } else if (listStack.length === 2 && indent < 2) {
        // 从嵌套层回到顶层:先收掉嵌套列表(其 </li></ul>),再收父 <li>,再开新项
        closeOneList()
        closeLi()
        const parent = listStack[listStack.length - 1]
        out.push(`<li>${renderInline(itemText)}`)
        if (parent !== undefined) parent.liOpen = true
        continue
      } else {
        closeLi()
        out.push(`<li>${renderInline(itemText)}`)
        top.liOpen = true
        continue
      }
      out.push(`<li>${renderInline(itemText)}`)
      continue
    }

    const quote = /^>\s?(.*)$/.exec(line)
    if (quote !== null) {
      flushParagraph()
      closeAllLists()
      out.push(`<blockquote>${renderInline(quote[1])}</blockquote>`)
      continue
    }

    paragraph.push(line.trim())
  }

  if (inCode) out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  flushParagraph()
  closeAllLists()
  return out.join('\n')
}
