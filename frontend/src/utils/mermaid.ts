/**
 * Mermaid 懒加载渲染:答案/笔记里出现 ```mermaid 代码块时,把它替换成 SVG。
 *
 * 为什么动态 import:mermaid 体积大(~700KB gzip),静态引入会把主包撑大;
 * 用 `await import('mermaid')` 后 Vite 拆成独立 chunk,用户真的看到图表才下载。
 *
 * 安全:securityLevel 'strict'(mermaid 自身消毒,不执行注入的脚本)。
 * 容错:单个块解析失败 → 保留原代码块 + 错误徽章,绝不抛错让整页挂掉。
 *
 * 交互:点图开灯箱放大(+/−/重置/Esc);useMaxWidth=false 让图按自然尺寸出,
 * 宽图横向滚动而不是被压小,节点文字不因缩放测量偏差被裁。
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
        fontFamily: "'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif",
        flowchart: { useMaxWidth: false, htmlLabels: true, padding: 14 },
      })
      return mermaid
    })
  }
  return mermaidPromise
}

// ---------------- 灯箱放大 ----------------

let lightboxEl: HTMLElement | null = null
let lightboxKeyHandler: ((e: KeyboardEvent) => void) | null = null
let lightboxScale = 1

function applyLightboxScale(): void {
  const canvas = lightboxEl?.querySelector<HTMLElement>('.mermaid-lightbox-canvas')
  if (canvas !== undefined && canvas !== null) canvas.style.zoom = String(lightboxScale)
}

function closeLightbox(): void {
  if (lightboxEl === null) return
  if (lightboxKeyHandler !== null) {
    document.removeEventListener('keydown', lightboxKeyHandler)
    lightboxKeyHandler = null
  }
  lightboxEl.remove()
  lightboxEl = null
}

function openLightbox(svg: SVGSVGElement): void {
  closeLightbox()
  lightboxScale = 1

  lightboxEl = document.createElement('div')
  lightboxEl.className = 'mermaid-lightbox'
  lightboxEl.innerHTML = `
    <div class="mermaid-lightbox-toolbar">
      <button type="button" data-act="minus" aria-label="缩小">−</button>
      <button type="button" data-act="reset">重置</button>
      <button type="button" data-act="plus" aria-label="放大">＋</button>
      <button type="button" data-act="close" class="mermaid-lightbox-close">关闭 (Esc)</button>
    </div>
    <div class="mermaid-lightbox-body">
      <div class="mermaid-lightbox-canvas"></div>
    </div>`

  const canvas = lightboxEl.querySelector('.mermaid-lightbox-canvas')
  canvas?.appendChild(svg.cloneNode(true))

  lightboxEl.addEventListener('click', (event) => {
    const target = event.target as HTMLElement
    const act = target.closest<HTMLElement>('[data-act]')?.dataset.act
    if (act === 'plus') {
      lightboxScale = Math.min(3, lightboxScale + 0.25)
      applyLightboxScale()
    } else if (act === 'minus') {
      lightboxScale = Math.max(0.5, lightboxScale - 0.25)
      applyLightboxScale()
    } else if (act === 'reset') {
      lightboxScale = 1
      applyLightboxScale()
    } else if (act === 'close' || target === lightboxEl) {
      closeLightbox() // 点背景空白处也关
    }
  })

  lightboxKeyHandler = (event: KeyboardEvent) => {
    if (event.key === 'Escape') closeLightbox()
  }
  document.addEventListener('keydown', lightboxKeyHandler)

  document.body.appendChild(lightboxEl)
  applyLightboxScale()
}

/** 全局只挂一次的点击委托:点任何 .mermaid-figure 开灯箱 */
let clickBound = false

function bindFigureClick(): void {
  if (clickBound) return
  clickBound = true
  document.addEventListener('click', (event) => {
    const target = event.target as HTMLElement
    const figure = target.closest<HTMLElement>('.mermaid-figure')
    if (figure === null || lightboxEl !== null) return
    const svg = figure.querySelector('svg')
    if (svg !== null) {
      event.preventDefault()
      openLightbox(svg)
    }
  })
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
 * 成功 → 用 `<div class="mermaid-figure">SVG</div>` 替换整个 pre(点击可放大);
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

  // 离屏宽容器里渲染:mermaid 的文字换行/测量受容器宽度影响,
  // 在窄气泡里渲染会把长标签硬裁;先在 1600px 的画布上排好再搬回来
  const offscreen = document.createElement('div')
  offscreen.style.cssText = 'position:absolute;left:-99999px;top:0;width:1600px;'
  document.body.appendChild(offscreen)

  for (const pre of targets) {
    const codeEl = pre.querySelector('code')
    const source = codeEl?.textContent ?? ''
    pre.dataset.mermaidDone = '1'
    try {
      blockSeq += 1
      const { svg } = await mermaid.render(`mmd-${seq}-${blockSeq}`, source, offscreen)
      const figure = document.createElement('div')
      figure.className = 'mermaid-figure'
      figure.title = '点击放大查看'
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
  offscreen.remove()
  bindFigureClick()
}
