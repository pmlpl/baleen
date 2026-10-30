/**
 * 极简 markdown 渲染器(手写 —— 表格/嵌套列表也是手写;mermaid 走独立懒加载模块)。
 * 支持:标题/段落/有序无序列表(一级嵌套)/表格/引用/代码块/行内 code 粗体 斜体/水平线/引用标记。
 *
 * 安全:所有原文先 HTML 转义再拼标记,即便原文含 <script> 也只显示成文字。
 * NoteView(笔记正文)与 QaView(问答答案)共用。
 */

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderInline(raw: string): string {
  const escaped = escapeHtml(raw)

  // 先切出行内代码,受保护不参与后续替换(否则 `arr[1]` 里的 [1] 会被当成引用)
  const codes: string[] = []
  const withPlaceholders = escaped.replace(/`([^`]+)`/g, (_m, body: string) => {
    codes.push(`<code>${body}</code>`)
    return `\u0000CODE${codes.length - 1}\u0000`
  })

  const withRefs = withPlaceholders
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

  // 还原行内代码
  return withRefs.replace(/\u0000CODE(\d+)\u0000/g, (_m, i: string) => codes[Number(i)] ?? '')
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
    const ordered = /^\s*\d+\.\s+(.*)$/.exec(line)
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line)
    const listItem = ordered ?? bullet
    if (listItem !== null) {
      flushParagraph()
      const tag = ordered === null ? 'ul' : 'ol'
      const top = listStack[listStack.length - 1]
      const nested = indent >= 2 && listStack.length === 1
      if (top === undefined) {
        out.push(`<${tag}>`)
        listStack.push({ tag, liOpen: true })
      } else if (nested) {
        // 关键:父 <li> 保持开放(不 closeLi),子列表才能嵌在它内部
        out.push(`<${tag}>`)
        listStack.push({ tag, liOpen: true })
      } else if (listStack.length === 2 && indent < 2) {
        // 从嵌套层回到顶层:先收掉嵌套列表(其 </li></ul>),再收父 <li>,再开新项
        closeOneList()
        closeLi()
        const parent = listStack[listStack.length - 1]
        out.push(`<li>${renderInline(listItem[1])}`)
        if (parent !== undefined) parent.liOpen = true
        continue
      } else {
        closeLi()
        out.push(`<li>${renderInline(listItem[1])}`)
        top.liOpen = true
        continue
      }
      out.push(`<li>${renderInline(listItem[1])}`)
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
