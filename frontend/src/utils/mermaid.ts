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
        flowchart: {
          useMaxWidth: false,
          htmlLabels: true,
          padding: 14,
          // v12 会给顶点标签钳 max-width(默认按 node.width-2*padding 算,实测只有 120px),
          // 再用 fastdom 异步量宽决定走 nowrap 还是换行路径——rAF 时机不稳时走 nowrap,
          // 长中文标签就被硬裁(根因 #2,与窄容器测量是两个独立问题)。
          // wrappingWidth 抬到 500:标签 ≤500px 按自然宽度展开,更长的自动换行,两条路径都不裁。
          wrappingWidth: 500,
        },
      })
      return mermaid
    })
  }
  return mermaidPromise
}

// ---------------- 灯箱放大 ----------------

let lightboxEl: HTMLElement | null = null
let lightboxKeyHandler: ((e: KeyboardEvent) => void) | null = null
let lightboxWheelHandler: ((e: WheelEvent) => void) | null = null
let lightboxScale = 1
/** 平移量(px):相对 canvas 原位。灯箱里缩放与平移都用 transform 一套坐标系 */
let lightboxX = 0
let lightboxY = 0

function applyLightboxTransform(): void {
  const canvas = lightboxEl?.querySelector<HTMLElement>('.mermaid-lightbox-canvas')
  if (canvas !== null && canvas !== undefined) {
    // transform-origin 保持 center:缩放以画布中心为基准,平移量独立叠加
    canvas.style.transform = `translate(${lightboxX}px, ${lightboxY}px) scale(${lightboxScale})`
  }
  // 放大后光标提示可拖;回到 1 倍且未平移时恢复
  const body = lightboxEl?.querySelector<HTMLElement>('.mermaid-lightbox-body')
  if (body !== null && body !== undefined) {
    const panned = lightboxX !== 0 || lightboxY !== 0
    body.classList.toggle('is-pannable', lightboxScale > 1 || panned)
  }
}

function resetLightboxTransform(): void {
  lightboxScale = 1
  lightboxX = 0
  lightboxY = 0
  applyLightboxTransform()
}

function closeLightbox(): void {
  if (lightboxEl === null) return
  if (lightboxKeyHandler !== null) {
    document.removeEventListener('keydown', lightboxKeyHandler)
    lightboxKeyHandler = null
  }
  const body = lightboxEl.querySelector<HTMLElement>('.mermaid-lightbox-body')
  if (body !== null) {
    if (lightboxWheelHandler !== null) {
      body.removeEventListener('wheel', lightboxWheelHandler)
      lightboxWheelHandler = null
    }
    body.removeEventListener('pointerdown', onLightboxPointerDown)
    body.removeEventListener('pointermove', onLightboxPointerMove)
    body.removeEventListener('pointerup', onLightboxPointerUp)
    body.removeEventListener('pointercancel', onLightboxPointerUp)
  }
  lightboxEl.remove()
  lightboxEl = null
}

/** ---- 拖拽平移(指针事件,鼠标/触摸/笔通用) ---- */
let dragState: {
  pointerId: number
  startX: number
  startY: number
  originX: number
  originY: number
  /** 累计位移:用来区分「拖动」与「点击背景关闭」 */
  moved: number
} | null = null

function onLightboxPointerDown(event: PointerEvent): void {
  // 只处理主键(右键/中键不拖)
  if (event.button !== 0) return
  const body = lightboxEl?.querySelector<HTMLElement>('.mermaid-lightbox-body')
  if (body === null || body === undefined) return
  dragState = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    originX: lightboxX,
    originY: lightboxY,
    moved: 0,
  }
  body.setPointerCapture(event.pointerId)
  body.classList.add('is-dragging')
}

function onLightboxPointerMove(event: PointerEvent): void {
  if (dragState === null || event.pointerId !== dragState.pointerId) return
  const dx = event.clientX - dragState.startX
  const dy = event.clientY - dragState.startY
  lightboxX = dragState.originX + dx
  lightboxY = dragState.originY + dy
  dragState.moved = Math.max(dragState.moved, Math.hypot(dx, dy))
  applyLightboxTransform()
}

/** 上一次拖拽的位移量:> 4px 视为拖动,随后的 click 不算「点空白关闭」 */
let lastDragMoved = 0

function onLightboxPointerUp(event: PointerEvent): void {
  if (dragState === null || event.pointerId !== dragState.pointerId) return
  const body = lightboxEl?.querySelector<HTMLElement>('.mermaid-lightbox-body')
  if (body !== null && body !== undefined) {
    body.releasePointerCapture?.(event.pointerId)
    body.classList.remove('is-dragging')
  }
  lastDragMoved = dragState.moved
  dragState = null
}

function openLightbox(svg: SVGSVGElement): void {
  closeLightbox()
  lightboxScale = 1
  lightboxX = 0
  lightboxY = 0

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

  // 滚轮缩放:以鼠标位置为锚点(放大时光标下的那一点不动)
  const bodyEl = lightboxEl.querySelector<HTMLElement>('.mermaid-lightbox-body')
  if (bodyEl !== null) {
    lightboxWheelHandler = (event: WheelEvent) => {
      event.preventDefault()
      const prev = lightboxScale
      const next = Math.min(3, Math.max(0.5, prev - event.deltaY * 0.0015))
      if (next === prev) return

      // 锚点换算:光标相对容器中心的偏移,按缩放比例差补偿平移
      const rect = bodyEl.getBoundingClientRect()
      const cx = event.clientX - rect.left - rect.width / 2
      const cy = event.clientY - rect.top - rect.height / 2
      const ratio = next / prev
      lightboxX = cx - (cx - lightboxX) * ratio
      lightboxY = cy - (cy - lightboxY) * ratio
      lightboxScale = next
      applyLightboxTransform()
    }
    bodyEl.addEventListener('wheel', lightboxWheelHandler, { passive: false })

    bodyEl.addEventListener('pointerdown', onLightboxPointerDown)
    bodyEl.addEventListener('pointermove', onLightboxPointerMove)
    bodyEl.addEventListener('pointerup', onLightboxPointerUp)
    bodyEl.addEventListener('pointercancel', onLightboxPointerUp)
  }

  lightboxEl.addEventListener('click', (event) => {
    const target = event.target as HTMLElement
    const act = target.closest<HTMLElement>('[data-act]')?.dataset.act
    // 拖动结束的松手也会派发 click:位移超过阈值就吞掉,别把灯箱关掉
    const wasDrag = lastDragMoved > 4
    lastDragMoved = 0

    if (act === 'plus') {
      lightboxScale = Math.min(3, lightboxScale + 0.25)
      applyLightboxTransform()
    } else if (act === 'minus') {
      lightboxScale = Math.max(0.5, lightboxScale - 0.25)
      applyLightboxTransform()
    } else if (act === 'reset') {
      resetLightboxTransform()
    } else if (act === 'close') {
      closeLightbox()
    } else if (target === lightboxEl && !wasDrag) {
      closeLightbox() // 点背景空白处也关;拖完松手不算
    }
  })

  lightboxKeyHandler = (event: KeyboardEvent) => {
    if (event.key === 'Escape') closeLightbox()
  }
  document.addEventListener('keydown', lightboxKeyHandler)

  document.body.appendChild(lightboxEl)
  applyLightboxTransform()
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
