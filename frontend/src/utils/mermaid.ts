/**
 * 图表渲染:答案/笔记里出现 ```mermaid 代码块时,把它替换成 SVG。
 *
 * **flowchart 优先走 Graphviz WASM**(见 `tryRenderViaGraphviz`):mermaid 依赖浏览器
 * 量文字,同一份内容在主人机器上渲染成空白框/只剩一个节点,我这里却完全正常,两轮
 * 判据修补都没治好。Graphviz 在 WASM 里算布局,不读浏览器测量 —— 从机制上绕开。
 * mermaid 保留为回退:转换器不支持的语法(时序图/甘特/饼图等)照旧交给它。
 *
 * 体积纪律:两条路径都懒加载 —— 用户真的看到图才下载对应 chunk。
 *
 * 安全:securityLevel 'strict'(mermaid 自身消毒,不执行注入的脚本)。
 * 容错:单个块解析失败 → 保留原代码块 + 错误徽章,绝不抛错让整页挂掉。
 *
 * 交互:点图开灯箱放大(+/−/重置/Esc);useMaxWidth=false 让图按自然尺寸出,
 * 宽图横向滚动而不是被压小,节点文字不因缩放测量偏差被裁。
 */

import { looksLikeFlowchart, mermaidFlowchartToDot } from './mermaidToDot'
import { renderDotToSvg } from './viz'

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
  // cloneNode 会把内联 transform 一起复制 —— 如果图在页面里被拖动/缩放过,
  // 灯箱里就会接着那个状态显示(图跑到视野外 = 「放大后看不见图」)。
  // 灯箱应该是干净的原始视图,所以显式清掉 transform。
  const clone = svg.cloneNode(true) as SVGSVGElement
  clone.style.transform = ''
  clone.style.transformOrigin = ''
  clone.style.maxWidth = 'none'
  clone.style.maxHeight = 'none'
  canvas?.appendChild(clone)

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
/** 拖拽刚结束 / 双击已处理时置位,让紧随其后的 click 不要开灯箱 */
let suppressFigureClick = false
/** 待开的灯箱定时器:双击复位要能取消它 */
let pendingLightboxTimer: number | null = null

/** 双击判定的时间窗(ms)。在这个窗口内的第二次落点算双击 */
const DOUBLE_TAP_MS = 340

function cancelPendingLightbox(): void {
  if (pendingLightboxTimer !== null) {
    window.clearTimeout(pendingLightboxTimer)
    pendingLightboxTimer = null
  }
}

function bindFigureClick(): void {
  if (clickBound) return
  clickBound = true
  document.addEventListener('click', (event) => {
    const target = event.target as HTMLElement
    const figure = target.closest<HTMLElement>('.mermaid-figure')
    if (figure === null || lightboxEl !== null) return
    if (suppressFigureClick) {
      suppressFigureClick = false
      return
    }
    const svg = figure.querySelector('svg')
    if (svg === null) return

    event.preventDefault()
    // **延迟开灯箱**:如果立刻开,双击的第二次点击会落到灯箱上而不是图上,
    // 于是「双击复位」永远收不到第二次点击(实测:第一次点击就把灯箱打开了)。
    // 留一个等于双击时间窗的延迟,双击时由 pointerup 取消这次开灯箱。
    cancelPendingLightbox()
    pendingLightboxTimer = window.setTimeout(() => {
      pendingLightboxTimer = null
      if (lightboxEl === null) openLightbox(svg)
    }, DOUBLE_TAP_MS)
  })
}

/**
 * Graphviz 出的静态 SVG 补上「抓拖 + 滚轮缩放」——和思维导图同样的手感。
 *
 * 为什么必须补(主人 2026-11-27 反馈):「思维导图就有点击交互,问什么这个就没有?」
 * markmap 自带 fold/zoom/pan,而 Graphviz 只吐一张静态 SVG,不给任何手势。
 * 流程图的字号被限高约束后会变小,没有缩放就等于看不全,所以这里补最小可用的一套:
 * - 滚轮缩放(以光标为锚点,和灯箱同一套换算)
 * - 按住拖动平移
 * - 双击复位
 * 单击仍走全局委托开灯箱看大图;拖动后松手的那次 click 会被抑制,不会误开。
 */
function enableZoomPan(figure: HTMLElement, svg: SVGSVGElement): void {
  let scale = 1
  let tx = 0
  let ty = 0
  const MIN_SCALE = 0.5
  const MAX_SCALE = 4

  const apply = (): void => {
    svg.style.transformOrigin = '0 0'
    svg.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`
    figure.classList.toggle('is-zoomed', scale !== 1 || tx !== 0 || ty !== 0)
  }

  const reset = (): void => {
    scale = 1
    tx = 0
    ty = 0
    apply()
  }

  figure.addEventListener(
    'wheel',
    (event: WheelEvent) => {
      if (!event.ctrlKey && Math.abs(event.deltaY) < 1) return
      event.preventDefault()
      const rect = figure.getBoundingClientRect()
      const cx = event.clientX - rect.left - tx
      const cy = event.clientY - rect.top - ty
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale - event.deltaY * 0.0018))
      if (next === scale) return
      const ratio = next / scale
      // 以光标为锚点:光标下那一点保持不动
      tx -= cx * (ratio - 1)
      ty -= cy * (ratio - 1)
      scale = next
      apply()
    },
    { passive: false },
  )

  let drag: { id: number; x: number; y: number; tx: number; ty: number; moved: number } | null =
    null
  /** 上一次「按下未拖动」的时刻,用来手判双击 */
  let lastTapAt = 0

  figure.addEventListener('pointerdown', (event: PointerEvent) => {
    if (event.button !== 0) return
    // 第二次按下(双击的第二次)不重新开始拖拽 —— 否则会把 dblclick 吃掉
    if (event.detail >= 2) return
    drag = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      tx,
      ty,
      moved: 0,
    }
    figure.setPointerCapture?.(event.pointerId)
  })

  figure.addEventListener('pointermove', (event: PointerEvent) => {
    if (drag === null || event.pointerId !== drag.id) return
    const dx = event.clientX - drag.x
    const dy = event.clientY - drag.y
    drag.moved = Math.max(drag.moved, Math.hypot(dx, dy))
    if (drag.moved < 4) return // 抖动不算拖拽
    tx = drag.tx + dx
    ty = drag.ty + dy
    figure.classList.add('is-panning')
    apply()
  })

  const endDrag = (event: PointerEvent): void => {
    if (drag === null || event.pointerId !== drag.id) return
    const wasDrag = drag.moved >= 4
    figure.releasePointerCapture?.(event.pointerId)
    figure.classList.remove('is-panning')
    drag = null
    // 真拖动过 → 抑制随后那次 click(否则一拖就弹灯箱)
    if (wasDrag) {
      suppressFigureClick = true
      return
    }

    // 双击复位:**在 pointerup 里自己数间隔**。
    //
    // 为什么不用原生 dblclick / click 事件:pointerdown 里调了 setPointerCapture,
    // 实测这两个事件都会被吞掉(Playwright 的 dblclick 根本收不到,
    // 手动派发 click 也不到)。pointerup 一定到,所以在这里计时最稳。
    const now = performance.now()
    if (now - lastTapAt < DOUBLE_TAP_MS) {
      reset()
      lastTapAt = 0
      suppressFigureClick = true // 双击的第二次不再开灯箱
      cancelPendingLightbox() // 第一次点击排的那次开灯箱也要取消
      return
    }
    lastTapAt = now
  }
  figure.addEventListener('pointerup', endDrag)
  figure.addEventListener('pointercancel', endDrag)

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
 * 一张渲染出来的图是否「自洽」——内容是画出来了,还是整张图被缩成一个点。
 *
 * **存在理由(2026-11-27 主人现场)**:一条 flowchart 渲染成 1746×883 的空白框,
 * 整张图的内容缩成 22×7 的小点(像素取证:框内只有一处 17×2 的墨迹)。凭截图无法
 * 判定是测量错还是样式错,而且无头环境 30 次冷启动 + 流式原文全量复跑都全绿,
 * 抓不到根因。但失败形态是可判定的,所以按「坏图不许上屏」兜底。
 *
 * **判据必须是「节点在屏幕上的实际渲染尺寸」,不能是 bbox/画布比例**:
 * 第一版用 `内容 bbox 面积 / 声明画布面积 >= 0.05`,被实测打穿 —— 把 viewBox 放大
 * 12 倍(内容坐标不变、视觉上正是「大框 + 小点」)后,比例算的是**用户坐标**,仍然
 * ≈0.94 → 判为正常,而节点渲染高度已从 48px 掉到 4px。用户坐标里的比例骗人,
 * 屏幕像素不会。
 *
 * 阈值依据(实测):正常图最小节点高 45-48px、宽 ≥150px;塌陷图节点高 ~4px、
 * 中位宽 ~14px。差一个数量级,取 12px 有足够余量(小字图也不会误伤)。
 */
const MIN_NODE_PX = 12

/** 'unknown' = 元素还没布局(视图被 keep-alive 藏起来时 getBoundingClientRect 恒为 0),
 *  这时既不能判好也不能判坏 —— 必须等它可见再判,否则会把好图误降级成代码块。 */
export type SanityVerdict = 'ok' | 'bad' | 'unknown'

export function judgeMermaidSvg(svg: SVGSVGElement): SanityVerdict {
  // 屏幕像素口径:getBoundingClientRect 已含所有祖先 transform,是用户真正看到的大小
  const rect = svg.getBoundingClientRect()
  if (!(rect.width > 0) || !(rect.height > 0)) return 'unknown'

  // 元素颗粒度按图类型取:mermaid 各图型的 class 完全不同。
  // - flowchart / state:`.node`(Graphviz 输出也是 `g.node`)
  // - sequenceDiagram:`text.actor`、`text.messageText`,**没有 .node**
  // - pie:`text.slice`、`.pieTitleText`,**没有 .node**
  // 早期版本只认 `.node`,导致时序图/饼图 100% 被误判成坏图降级(db31916 引入,
  // 2026-11-27 实测确认:HEAD 版本时序图与饼图都退回代码块,而 mermaid 本身渲染正常)。
  // 所以改成「取实际画出来的最小可见元素」,不绑定某一种图型的 class。
  const nodes = Array.from(
    svg.querySelectorAll<SVGGraphicsElement>(
      '.node, text.actor, text.messageText, text.slice, text.label, .pieTitleText, .taskText, .sectionTitle',
    ),
  )
    .map((el) => el.getBoundingClientRect())
    .filter((box) => box.width > 0 && box.height > 0)

  // 一个可见元素都没有 → 布局没跑起来(真空图)
  if (nodes.length === 0) return 'bad'

  // 判据只看「高度」:塌缩时所有元素一起等比缩,高度是最稳的信号。
  //
  // **不要加「最宽元素必须够宽」这类条件** —— 时序图/饼图天然就窄(实测
  // sequenceDiagram 的 actor 文字只有 9-11px 宽、messageText 34px),按宽度卡
  // 会把整类图误判成坏图。db31916 就踩过:门槛 36px,而时序图最宽才 34px,
  // 差 2px 全量降级。宽度对「这张图是不是画出来了」没有判别力,高度才有。
  for (const box of nodes) {
    if (box.height < MIN_NODE_PX) return 'bad'
  }
  return 'ok'
}

/** 布尔口径:'unknown' 按「未证明是坏图」处理 */
export function isSaneMermaidSvg(svg: SVGSVGElement): boolean {
  return judgeMermaidSvg(svg) !== 'bad'
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

/** 降级:退回代码块 + 角标(用户至少能看到 `flowchart TD ...` 原文) */
function degradeToSource(pre: HTMLPreElement, figure: HTMLElement, text: string): void {
  figure.replaceWith(pre)
  pre.classList.add('mermaid-error')
  if (pre.querySelector('.mermaid-error-badge') === null) {
    const badge = document.createElement('span')
    badge.className = 'mermaid-error-badge'
    badge.textContent = text
    pre.prepend(badge)
  }
}

/**
 * 视图被藏起来时先放行,等它可见再判一次 —— 藏着的图量不到像素,
 * 直接判坏会把好图降级成代码块(keep-alive 切页时答案还在流式,必然撞上)。
 */
function recheckWhenVisible(figure: HTMLElement, pre: HTMLPreElement): void {
  if (typeof IntersectionObserver === 'undefined') return
  const io = new IntersectionObserver((entries) => {
    if (!entries.some((e) => e.isIntersecting)) return
    io.disconnect()
    const svgEl = figure.querySelector('svg')
    if (svgEl !== null && judgeMermaidSvg(svgEl) === 'bad') {
      degradeToSource(pre, figure, '图表渲染异常,按代码显示')
    }
  })
  io.observe(figure)
}

/**
 * 让 svg 的声明画布**恰好等于内容**——从根上消掉「大框 + 小点」这种形态。
 *
 * **为什么必须做,而不是只靠 isSane 判坏**(2026-11-27 主人三次现场):
 * 主人截图里的框是 1536×888,框内内容缩成一个点;而我这边同一段代码渲染出来是
 * 459×540 的正常图。也就是说 mermaid 有时会给出一个**远大于内容的声明画布**
 * (viewBox / width / height 三者之一被撑大),内容坐标没错、视觉上却成了一粒沙。
 * 这个形态我无头复现不出来(30 次冷启动 + 流式原文 + 并发全绿),所以不去赌
 * 触发条件,而是不管画布被撑成什么样,**一律按内容 bbox 重新声明**:
 *
 *   viewBox = 内容 bbox(留 8px 余量),width/height = bbox 尺寸 + 余量
 *
 * 内容永远填满自己的框,即使 mermaid 算错了画布也不会露馅。对正常图是近似恒等
 * 变换(实测 viewBox `4 4 458 540` → `4 4 443 524`,内容一样大,只少了一圈无效留白)。
 *
 * 返回 false 表示量不到内容(未布局/空图),由调用方按 unknown/bad 处理。
 */
const FIT_PADDING = 8

export function fitSvgToContent(svg: SVGSVGElement): boolean {
  let box: DOMRect
  try {
    box = svg.getBBox() // 含所有子元素的内容实际范围(用户坐标)
  } catch {
    return false
  }
  if (!(box.width > 0) || !(box.height > 0)) return false

  const x = box.x - FIT_PADDING
  const y = box.y - FIT_PADDING
  const w = box.width + FIT_PADDING * 2
  const h = box.height + FIT_PADDING * 2
  svg.setAttribute('viewBox', `${x} ${y} ${w} ${h}`)
  svg.setAttribute('width', String(Math.round(w)))
  svg.setAttribute('height', String(Math.round(h)))
  // mermaid 有时会挂 max-width(useMaxWidth 分支);不摘掉的话框还是被限死
  svg.style.maxWidth = 'none'
  return true
}

/**
 * **优先路径**:flowchart 交给 Graphviz WASM 渲染。
 *
 * 返回 true = 已成功画进 DOM;false = 不支持/失败,调用方回退 mermaid。
 *
 * 为什么优先走这条(2026-11-27):mermaid 依赖浏览器量文字,同一份内容在主人机器上
 * 渲染成空白框/只剩一个节点,在我这里完全正常,两轮防护都没治好。Graphviz 的布局
 * 在 WASM 里算,不读浏览器测量,从机制上没有那个差异源。
 */
async function tryRenderViaGraphviz(pre: HTMLPreElement, source: string): Promise<boolean> {
  if (!looksLikeFlowchart(source)) return false
  let svg: string
  try {
    svg = await renderDotToSvg(mermaidFlowchartToDot(source))
  } catch {
    return false // 语法不在转换器支持范围 → 交还给 mermaid
  }
  const figure = document.createElement('div')
  figure.className = 'mermaid-figure mermaid-figure--graphviz'
  figure.title = '滚轮缩放 / 拖动平移 / 双击复位 / 单击看大图'
  figure.innerHTML = svg
  pre.replaceWith(figure)
  await nextFrame()

  // 同一套像素判据兜底:万一 Graphviz 输出也有问题(比如字体缺字),按坏图处理
  const svgEl = figure.querySelector('svg')
  if (svgEl !== null && judgeMermaidSvg(svgEl) !== 'bad') {
    bindFigureClick()
    enableZoomPan(figure, svgEl) // 静态 SVG 补手势,和思维导图手感一致
    return true
  }
  figure.replaceWith(pre)
  return false
}

/** 单块渲染:成功→figure 已替换进 DOM;坏图/异常→pre 留在原位并带角标 */
export async function renderMermaidBlock(
  pre: HTMLPreElement,
  source: string,
  mermaid: MermaidRenderer,
  seq: number,
  offscreen: HTMLElement,
): Promise<void> {
  // 先试 Graphviz(WASM 布局,不受浏览器量字影响);不行再走 mermaid
  if (await tryRenderViaGraphviz(pre, source)) return

  // 两次机会:第一次可能撞上字体未就绪/容器尚未布局,重试通常就好了
  for (let attempt = 0; attempt < 2; attempt += 1) {
    blockSeq += 1
    const { svg } = await mermaid.render(`mmd-${seq}-${blockSeq}`, source, offscreen)
    const figure = document.createElement('div')
    figure.className = 'mermaid-figure'
    figure.title = '点击放大查看'
    figure.innerHTML = svg // mermaid strict 模式输出已消毒的 SVG

    // 必须先入 DOM 且完成一次布局再量:游离节点的 rect 恒为 0,会把好图误判成坏图
    pre.replaceWith(figure)
    await nextFrame()
    const svgEl = figure.querySelector('svg')

    // 关键一步:**按内容重新声明画布**。mermaid 偶尔给出远大于内容的画布
    // (主人现场:1536×888 的框里内容缩成一个点),这里一律拉回内容尺寸,
    // 良性情况下是近似恒等变换。
    if (svgEl !== null) {
      fitSvgToContent(svgEl)
      await nextFrame() // 改完 viewBox 要重新布局才量得准
    }

    const verdict = svgEl === null ? 'bad' : judgeMermaidSvg(svgEl)
    if (verdict === 'ok') return
    if (verdict === 'unknown') {
      recheckWhenVisible(figure, pre) // 藏着的先留着,可见时再判
      return
    }

    figure.replaceWith(pre) // 坏图退回代码块,准备重试
    if (attempt === 0) await nextFrame()
  }
  degradeToSource(pre, pre, '图表渲染异常,按代码显示')
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
