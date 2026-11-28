/**
 * 笔记导出:自包含 HTML 单文件(双击离线可读,```demo 交互演示照常可玩)。
 *
 * 产出纪律(2026-11-28,理科交互演示批):
 * - 正文取**渲染完 DOM 的快照**(clone contentEl):mermaid/mindmap/SVG 已是 <svg> 节点,
 *   demo 是带 srcdoc 的沙箱 iframe,序列化后原样复生 —— 渲染逻辑零重复,前端唯一真源;
 * - 图片 fetch 成 data URI 内联(离线不裂);失败保持原 src,不阻塞导出;
 * - 思源宋体/KaTeX 走 CDN link(离线时字体回退系统宋体栈、公式退化为普通 HTML 排版,
 *   正文与 demo 都不受影响 —— demo 的 prompt 硬约束就是零外部资源,离线照常交互);
 * - 样式是纸墨排版的无作用域子集(NoteView 的 .sec 系列是 scoped,导出里带不上
 *   scope 属性,这里按同结构类名重写一份;改 NoteView 正文排版时同步这里)。
 */

const EXPORT_CSS = `
:root {
  --bg: #f4f2ec; --panel: #fffdf8; --ink: #211f1c; --ink-2: #5f594e; --ink-3: #9c9484;
  --line: #e6e1d3; --accent-soft: #e8eef2; --neutral-soft: #efece4;
  --font-serif: 'Noto Serif SC', 'Songti SC', 'STSong', 'SimSun', serif;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--font-serif); font-size: 15px; line-height: 1.9;
}
.note-export { max-width: 860px; margin: 0 auto; padding: 48px 28px 80px; }
.note-export > h1 {
  margin: 0 0 8px; font-size: 26px; font-weight: 700; letter-spacing: 0.02em;
  line-height: 1.4;
}
.note-export .export-meta { margin: 0 0 28px; color: var(--ink-3); font-size: 12px; }
.sec-block { padding-bottom: 26px; margin-bottom: 26px; border-bottom: 1px solid var(--line); }
.sec-block:last-of-type { border-bottom: none; }
.sec-heading {
  margin: 0 0 14px; color: var(--ink); font-family: var(--font-serif);
  font-size: 18px; font-weight: 600; letter-spacing: 0.02em; line-height: 1.4;
}
.sec-heading[data-depth='1'], .sec-heading[data-depth='2'] {
  padding-left: 10px; border-left: 3px solid var(--accent-soft);
}
.sec-heading[data-depth='1'] { font-size: 16px; }
.sec-heading[data-depth='2'] { color: var(--ink-2); font-size: 15px; }
.sec { color: var(--ink); font-size: 15px; line-height: 1.9; word-break: break-word; }
.sec p { margin: 0 0 14px; }
.sec p:last-child { margin-bottom: 0; }
.sec-code {
  margin: 0 0 16px; padding: 13px 16px; overflow-x: auto;
  border: 1px solid var(--line); border-radius: 8px; background: var(--neutral-soft);
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 13px; line-height: 1.7; white-space: pre;
}
.sec-code[data-lang]::before {
  content: attr(data-lang); display: block; margin-bottom: 6px;
  color: var(--ink-3); font-size: 11px; text-align: right;
}
.sec-code-inline {
  padding: 1px 6px; border-radius: 5px; background: var(--neutral-soft);
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace; font-size: 0.92em;
}
.sec-list { margin: 0 0 14px; padding-left: 1.8em; line-height: 1.75; }
.sec-list li { margin: 0 0 6px; }
.sec-quote {
  margin: 0 0 14px; padding: 6px 14px; border-left: 3px solid var(--accent-soft);
  border-radius: 0 8px 8px 0; background: var(--panel); color: var(--ink-2);
}
.note-img { margin: 0 0 16px; text-align: center; }
.note-img img {
  max-width: min(100%, 640px); border: 1px solid var(--line);
  border-radius: 8px; background: var(--panel);
}
table { margin: 14px 0; border-collapse: collapse; border: 1px solid var(--line); font-size: 13.5px; }
th, td { padding: 7px 12px; border: 1px solid var(--line); }
th { background: var(--neutral-soft); font-weight: 600; }
tbody tr:nth-child(even) { background: #f8f5ee; }
.mermaid-figure, .svg-figure, .demo-figure {
  margin: 14px 0; border: 1px solid var(--line); border-radius: 8px; background: var(--panel);
}
.mermaid-figure { padding: 12px; overflow-x: auto; }
.mermaid-figure svg, .svg-figure svg { max-width: 100%; }
.demo-frame, .svg-frame { display: block; width: 100%; border: none; border-radius: 8px; }
.katex-display { overflow-x: auto; }
a { color: var(--ink); }
`

/** 逐张把 <img> 的 src 换成 data URI(离线不裂);失败保持原 src 不阻塞 */
async function inlineImages(root: HTMLElement): Promise<void> {
  const imgs = Array.from(root.querySelectorAll('img'))
  for (const img of imgs) {
    const src = img.getAttribute('src') ?? ''
    if (!src || src.startsWith('data:')) continue
    try {
      const res = await fetch(src)
      if (!res.ok) continue
      const blob = await res.blob()
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result as string)
        reader.onerror = () => reject(reader.error)
        reader.readAsDataURL(blob)
      })
      img.setAttribute('src', dataUrl)
    } catch {
      // 网络/权限问题:留原 src(在线时仍能显示),其余内容照常导出
    }
  }
}

/** Windows 文件名安全化(与下载其他文件同款纪律) */
function safeFilename(title: string): string {
  return title.replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 60) || 'note'
}

/**
 * 导出一篇笔记为自包含 HTML 并触发下载。
 * ``contentEl`` 必须是**已完成渲染**的正文容器(mermaid/demo 都已成形)。
 */
export async function exportNoteHtml(title: string, contentEl: HTMLElement): Promise<void> {
  const clone = contentEl.cloneNode(true) as HTMLElement
  await inlineImages(clone)

  const html =
    `<!DOCTYPE html>\n<html lang="zh">\n<head>\n<meta charset="utf-8">\n` +
    `<meta name="viewport" content="width=device-width, initial-scale=1">\n` +
    `<title>${title}</title>\n` +
    `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fontsource/noto-serif-sc@5/400.css">\n` +
    `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fontsource/noto-serif-sc@5/700.css">\n` +
    `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.19.1/dist/katex.min.css">\n` +
    `<style>${EXPORT_CSS}</style>\n</head>\n<body>\n` +
    `<main class="note-export">\n<h1></h1>\n<p class="export-meta"></p>\n` +
    `${clone.innerHTML}\n</main>\n</body>\n</html>\n`

  // 标题走 textContent 注入,不吃 HTML 转义差(标题里可能有 < & 等字符)
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const h1 = doc.querySelector<HTMLElement>('.note-export > h1')
  if (h1 !== null) h1.textContent = title
  const meta = doc.querySelector<HTMLElement>('.note-export > .export-meta')
  if (meta !== null) meta.textContent = `导出自 baleen · ${new Date().toLocaleString('zh-CN')}`

  const blob = new Blob([`<!DOCTYPE html>\n${doc.documentElement.outerHTML}`], {
    type: 'text/html;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${safeFilename(title)}.html`
  a.click()
  URL.revokeObjectURL(url)
}
