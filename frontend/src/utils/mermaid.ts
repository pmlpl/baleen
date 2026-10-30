/**
 * Mermaid 懒加载渲染:答案/笔记里出现 ```mermaid 代码块时,把它替换成 SVG。
 *
 * 为什么动态 import:mermaid 体积大(~700KB gzip),静态引入会把主包撑大;
 * 用 `await import('mermaid')` 后 Vite 拆成独立 chunk,用户真的看到图表才下载。
 *
 * 安全:securityLevel 'strict'(mermaid 自身消毒,不执行注入的脚本)。
 * 容错:单个块解析失败 → 保留原代码块 + 错误徽章,绝不抛错让整页挂掉。
 */

let mermaidPromise: Promise<typeof import('mermaid').default> | null = null

async function loadMermaid(): Promise<typeof import('mermaid').default> {
  if (mermaidPromise === null) {
    mermaidPromise = import('mermaid').then((mod) => {
      const mermaid = mod.default
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'neutral',
        fontFamily: "'Segoe UI', 'Microsoft YaHei', sans-serif",
      })
      return mermaid
    })
  }
  return mermaidPromise
}

/** 判断一段代码是不是 mermaid 图(排除普通代码块被误渲染) */
function looksLikeMermaid(code: string): boolean {
  return /^\s*(graph|flowchart|mindmap|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|xychart-beta|quadrantChart|timeline|gitGraph|mindmap)\b/i.test(
    code,
  )
}

let renderSeq = 0
let blockSeq = 0

/**
 * 渲染 root 下所有 mermaid 代码块。
 *
 * 目标节点:QaView / NoteView 渲染 markdown 后产生的
 * `<pre><code>graph TD ...</code></pre>`(渲染器对 ``` 围栏的产物)。
 * 成功 → 用 `<div class="mermaid-figure">SVG</div>` 替换整个 pre;
 * 失败 → pre 原样保留,加一个「图表语法有误」角标。
 *
 * 幂等:已是 figure 的节点跳过,可安全重复调用。
 */
export async function renderMermaidIn(root: HTMLElement): Promise<void> {
  const blocks = Array.from(root.querySelectorAll<HTMLPreElement>('pre'))
  const targets = blocks.filter((pre) => {
    if (pre.dataset.mermaidDone === '1') return false
    const code = pre.querySelector('code')
    return code !== null && looksLikeMermaid(code.textContent ?? '')
  })
  if (targets.length === 0) return

  const mermaid = await loadMermaid()
  renderSeq += 1
  const seq = renderSeq

  for (const pre of targets) {
    const codeEl = pre.querySelector('code')
    const source = codeEl?.textContent ?? ''
    pre.dataset.mermaidDone = '1'
    try {
      blockSeq += 1
      const { svg } = await mermaid.render(`mmd-${seq}-${blockSeq}`, source)
      const figure = document.createElement('div')
      figure.className = 'mermaid-figure'
      figure.innerHTML = svg // mermaid strict 模式输出已消毒的 SVG
      pre.replaceWith(figure)
    } catch {
      pre.classList.add('mermaid-error')
      if (pre.querySelector('.mermaid-error-badge') === null) {
        const badge = document.createElement('span')
        badge.className = 'mermaid-error-badge'
        badge.textContent = '图表语法有误,按代码显示'
        pre.prepend(badge)
      }
    }
  }
}
