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

/**
 * 等字体就绪再量图。
 *
 * mermaid 靠 `getBBox`/文本度量定节点宽度,字体没落地时量到的是回退字体的尺寸,
 * 图会按错的尺寸排好并且**不会自己重排**。字体来自 jsdelivr CDN,主人日常关代理,
 * 这个窗口是常态而不是异常情况。
 *
 * 必须设上限:CDN 完全不可达时 `fonts.ready` 可能长期不 resolve,
 * 不能为了一张图把整段答案的渲染卡死。
 */
let fontsWaited = false
async function waitFontsOnce(timeoutMs = 2500): Promise<void> {
  if (fontsWaited || typeof document === 'undefined' || !('fonts' in document)) return
  fontsWaited = true
  await Promise.race([
    document.fonts.ready.then(() => undefined),
    new Promise<void>((resolve) => setTimeout(resolve, timeoutMs)),
  ])
}

/** 等两帧:让浏览器完成一次布局,重试前必须先把上一张坏图从 DOM 摘掉 */
function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  })
}

/**
 * 一张渲染出来的图是否「自洽」——内容是画出来了,还是画布大得离谱而内容缩成一点。
 *
 * **存在理由(2026-11-27 主人现场)**:一条 flowchart 渲染成 1746×883 的空白框,
 * 整张图的内容缩成 22×7 的小点(截图取证:框内只有一处 17×2 的墨迹)。凭截图无法
 * 判定是测量错还是样式错,而且**无头环境 30 次冷启动 + 流式原文全量复跑都全绿**,
 * 抓不到根因。但失败形态是可判定的:svg 声明画布远大于内容实际 bbox。
 *
 * 与其把这种「大片空白」摆到用户面前,不如按渲染失败处理:重试一次,仍坏就退回
 * 原始代码块 + 角标 —— 用户至少还能看到 `flowchart TD ...` 原文。
 *
 * 阈值 0.05 的依据:正常图的内容几乎填满 mermaid 自己给的画布(实测 fill ≈ 0.9,
 * 画布只比内容多一圈 padding),塌陷时 fill < 0.01,两者差两个数量级。
 */
export function isSaneMermaidSvg(svg: SVGSVGElement): boolean {
  const w = Number.parseFloat(svg.getAttribute('width') ?? '')
  const h = Number.parseFloat(svg.getAttribute('height') ?? '')
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return false
  // 一块图总得有节点/连线/文字之一;全空说明布局没跑起来
  if (svg.querySelector('.node, .edgePath, path.flowchart-link, text') === null) return false
  let box: DOMRect
  try {
    box = svg.getBBox() // 未挂载或零尺寸时浏览器会抛,直接按坏图算
  } catch {
    return false
  }
  if (!(box.width > 0) || !(box.height > 0)) return false
  return (box.width * box.height) / (w * h) >= 0.05
}

let renderSeq = 0
let blockSeq = 0

/** 只用得到 render 一个方法:收窄成结构化类型,探针能注入假渲染器验证降级,不必碰真 mermaid */
export interface MermaidRenderer {
  render(
    id: string,
    source: string,
    container: HTMLElement,
  ): Promise<{ svg: string }>
}

/** 单块渲染:成功→figure 已替换进 DOM;坏图/异常→pre 留在原位并带角标 */
export async function renderMermaidBlock(
  pre: HTMLPreElement,
  source: string,
  mermaid: MermaidRenderer,
  seq: number,
  offscreen: HTMLElement,
): Promise<void> {
  // 两次机会:第一次可能撞上字体未就绪/容器尚未布局,重试通常就好了
  for (let attempt = 0; attempt < 2; attempt += 1) {
    blockSeq += 1
    const { svg } = await mermaid.render(`mmd-${seq}-${blockSeq}`, source, offscreen)
    const figure = document.createElement('div')
    figure.className = 'mermaid-figure'
    figure.title = '点击放大查看'
    figure.innerHTML = svg // mermaid strict 模式输出已消毒的 SVG

    // 必须先入 DOM 再量:游离节点的 getBBox 恒为 0,会把好图误判成坏图
    pre.replaceWith(figure)
    const svgEl = figure.querySelector('svg')
    if (svgEl !== null && isSaneMermaidSvg(svgEl)) return

    figure.replaceWith(pre) // 坏图退回代码块,准备重试
    if (attempt === 0) await nextFrame()
  }
  pre.classList.add('mermaid-error')
  if (pre.querySelector('.mermaid-error-badge') === null) {
    const badge = document.createElement('span')
    badge.className = 'mermaid-error-badge'
    badge.textContent = '图表渲染异常,按代码显示'
    pre.prepend(badge)
  }
}

/**
 * 渲染 root 下所有 mermaid 代码块。
 *
 * 目标节点:QaView / NoteView 渲染 markdown 后产生的
 * `<pre><code>graph TD ...</code></pre>`(渲染器对 ``` 围栏的产物)。
 * 成功 → 用 `<div class="mermaid-figure">SVG</div>` 替换整个 pre(点击可放大);
 * 失败 → pre 原样保留,加一个角标(语法错 / 渲染异常)。
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

  // 标记先落,再 await:并发调用(流式结束 + 切回会话各触发一次)不能重复渲染同一块
  for (const pre of targets) pre.dataset.mermaidDone = '1'

  await waitFontsOnce()
  const mermaid = await loadMermaid()
  renderSeq += 1
  const seq = renderSeq

  // 离屏宽容器里渲染:mermaid 的文字换行/测量受容器宽度影响,
  // 在窄气泡里渲染会把长标签硬裁;先在 1600px 的画布上排好再搬回来
  const offscreen = document.createElement('div')
  offscreen.style.cssText = 'position:absolute;left:-99999px;top:0;width:1600px;'
  document.body.appendChild(offscreen)

  try {
    for (const pre of targets) {
      const source = pre.querySelector('code')?.textContent ?? ''
      try {
        await renderMermaidBlock(pre, source, mermaid, seq, offscreen)
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
  } finally {
    offscreen.remove()
  }
  bindFigureClick()
}
