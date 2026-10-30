/**
 * 极简 markdown 渲染器(手写,无第三方依赖 —— AGENTS.md §3.2 不许加 marked 之类)。
 * 支持:标题/段落/有序无序列表/引用/代码块/行内 code 粗体 斜体/水平线。
 *
 * 安全:所有原文先 HTML 转义再拼标记,即便原文含 <script> 也只显示成文字。
 * NoteView(笔记正文)与 QaView(问答答案)共用,从 NoteView 原本内联的实现抽出。
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

export function renderMarkdown(markdown: string): string {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []
  let listTag: 'ul' | 'ol' | null = null
  let inCode = false
  let codeLines: string[] = []
  let paragraph: string[] = []

  const flushParagraph = (): void => {
    if (paragraph.length === 0) return
    out.push(`<p>${renderInline(paragraph.join(' '))}</p>`)
    paragraph = []
  }

  const closeList = (): void => {
    if (listTag === null) return
    out.push(`</${listTag}>`)
    listTag = null
  }

  for (const line of lines) {
    if (line.trimStart().startsWith('```')) {
      if (inCode) {
        out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = []
        inCode = false
      } else {
        flushParagraph()
        closeList()
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
      closeList()
      continue
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading !== null) {
      flushParagraph()
      closeList()
      const level = heading[1].length
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
      continue
    }

    if (/^\s*([-*_])\1{2}\s*$/.test(line)) {
      flushParagraph()
      closeList()
      out.push('<hr />')
      continue
    }

    const ordered = /^\s*\d+\.\s+(.*)$/.exec(line)
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line)
    const listItem = ordered ?? bullet
    if (listItem !== null) {
      flushParagraph()
      const tag = ordered === null ? 'ul' : 'ol'
      if (listTag !== tag) {
        closeList()
        out.push(`<${tag}>`)
        listTag = tag
      }
      out.push(`<li>${renderInline(listItem[1])}</li>`)
      continue
    }

    const quote = /^>\s?(.*)$/.exec(line)
    if (quote !== null) {
      flushParagraph()
      closeList()
      out.push(`<blockquote>${renderInline(quote[1])}</blockquote>`)
      continue
    }

    paragraph.push(line.trim())
  }

  if (inCode) out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  flushParagraph()
  closeList()
  return out.join('\n')
}
