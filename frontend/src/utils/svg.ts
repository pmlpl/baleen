/**
 * SVG 插图渲染:答案/笔记里的 ```svg 代码块 → 沙箱 iframe 显示成矢量图。
 *
 * 安全(核心):模型的 SVG 是不可信输入 —— SVG 里能夹 <script>/事件处理器/外链请求。
 * 渲染走 <iframe srcdoc> + `sandbox`(不给 allow-scripts 也不给 allow-same-origin):
 * 只显图、零脚本、与主文档完全隔离;再往 srcdoc 里塞一条 CSP(default-src 'none')
 * 把外部资源请求也掐掉。不引 DOMPurify(引依赖要主人批,这个方案零依赖)。
 *
 * 尺寸:渲染前在主文档用 DOMParser 解析宽高/宽高比(DOMParser 只建树不执行脚本),
 * 按容器宽度预算好 iframe 高度——sandbox 里读不到内部文档,也不需要读。
 * 容错:解析失败 → 保留原代码块 + 角标(与 mermaid 失败同款),绝不抛错。
 */

/** 判断一段代码是不是自包含 SVG(排除普通代码被误渲染) */
function looksLikeSvg(code: string): boolean {
  const t = code.trim()
  if (!t.endsWith('</svg>')) return false
  return t.startsWith('<svg') || (t.startsWith('<?xml') && t.includes('<svg'))
}

interface SvgMetrics {
  ratio: number | null
  /** 固定声明的高度(px),如 height="400" */
  height: number | null
}

/** 用 DOMParser 量 SVG 宽高比(不执行任何内容;解析失败返回 null 尺寸) */
function measureSvg(code: string): SvgMetrics | null {
  const doc = new DOMParser().parseFromString(code, 'image/svg+xml')
  if (doc.querySelector('parsererror') !== null) return null
  const svg = doc.documentElement
  if (svg === null || svg.nodeName.toLowerCase() !== 'svg') return null
  const num = (value: string | null | undefined): number | null => {
    if (value === null || value === undefined) return null
    const m = /^\s*([\d.]+)(?:px)?\s*$/.exec(value)
    return m === null ? null : Number(m[1])
  }
  const w = num(svg.getAttribute('width'))
  const h = num(svg.getAttribute('height'))
  const ratio = w !== null && h !== null && w > 0 ? h / w : null
  if (ratio !== null) return { ratio, height: h }
  const viewBox = svg.getAttribute('viewBox')
  if (viewBox !== null) {
    const parts = viewBox.trim().split(/[\s,]+/).map(Number)
    if (parts.length === 4 && parts[2] > 0 && Number.isFinite(parts[3])) {
      return { ratio: parts[3]! / parts[2]!, height: h }
    }
  }
  return { ratio: null, height: h }
}

const MIN_HEIGHT = 120
const MAX_HEIGHT = 640
const FALLBACK_HEIGHT = 360

function srcdocHtml(svgText: string): string {
  return (
    '<!doctype html><html><head><meta charset="utf-8">'
    // CSP:除内联样式外一律禁(SVG 需要 style 属性/标签;外部图片/字体/脚本全断)
    + '<meta http-equiv="Content-Security-Policy" '
    + "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">"
    + '<style>html,body{margin:0;padding:0;background:transparent}'
    + 'body{display:flex;align-items:center;justify-content:center;overflow:hidden}'
    + 'svg{max-width:100%;height:auto}</style>'
    + `</head><body>${svgText}</body></html>`
  )
}

/**
 * 渲染 root 下所有 SVG 代码块。
 *
 * 目标节点:QaView / NoteView 渲染器对 ``` 围栏的产物 `<pre><code><svg …</code></pre>`。
 * 成功 → `<div class="svg-figure"><iframe …></div>` 替换整个 pre;
 * 失败 → pre 原样保留 + 「SVG 语法有误」角标。幂等:已处理的节点跳过,可安全重复调用。
 */
export async function renderSvgsIn(root: HTMLElement): Promise<void> {
  const blocks = Array.from(root.querySelectorAll<HTMLPreElement>('pre'))
  const targets = blocks.filter((pre) => {
    if (pre.dataset.svgDone === '1') return false
    const code = pre.querySelector('code')
    return code !== null && looksLikeSvg(code.textContent ?? '')
  })
  for (const pre of targets) {
    const codeEl = pre.querySelector('code')
    const source = (codeEl?.textContent ?? '').trim()
    pre.dataset.svgDone = '1'
    const metrics = measureSvg(source)
    if (metrics === null) {
      pre.classList.add('svg-error')
      if (pre.querySelector('.svg-error-badge') === null) {
        const badge = document.createElement('span')
        badge.className = 'svg-error-badge'
        badge.textContent = 'SVG 语法有误,按代码显示'
        pre.prepend(badge)
      }
      continue
    }
    // 容器可用宽度决定 iframe 高度;量不到容器就按 600px 预算
    const available = Math.max(240, (pre.parentElement?.clientWidth ?? 600) - 32)
    let height = FALLBACK_HEIGHT
    if (metrics.height !== null) {
      height = metrics.height
    } else if (metrics.ratio !== null) {
      height = Math.round(available * metrics.ratio)
    }
    height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, height))

    const figure = document.createElement('div')
    figure.className = 'svg-figure'
    const frame = document.createElement('iframe')
    frame.className = 'svg-frame'
    // sandbox 不带任何 allow-*:脚本/表单/同源访问全禁,纯静态展示
    frame.setAttribute('sandbox', '')
    frame.setAttribute('title', 'AI 生成的 SVG 插图')
    frame.setAttribute('loading', 'lazy')
    frame.style.height = `${height}px`
    frame.srcdoc = srcdocHtml(source)
    figure.appendChild(frame)
    pre.replaceWith(figure)
  }
}
