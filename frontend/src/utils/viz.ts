/**
 * Graphviz WASM 渲染:flowchart 的**替代渲染路径**。
 *
 * **存在理由**(2026-11-27,主人现场连续两轮未修复):
 * mermaid 把节点尺寸交给浏览器量文字。同一段 `.mermaid` 在主人机器上渲染成空白框
 * 或只剩一个节点,在我这里(同版本/同代码/同内容)完全正常 —— 差异发生在量文字那一步,
 * 事后判据再怎么改也只能补救,治不了。
 *
 * Graphviz 的布局在 WASM 里算完,**完全不读浏览器测量结果**。实测同一输入连渲 5 次
 * 尺寸字节级一致;而且无头环境(Playwright)能完整验证,不需要碰主人机器。
 *
 * 体积:gzip 约 468KB,与 mermaid(647KB)同量级,且**只在真的出现 flowchart 时才下载**
 * (与 mermaid 同样的懒加载纪律)。
 */

type VizInstance = Awaited<ReturnType<typeof import('@viz-js/viz').instance>>

let vizPromise: Promise<VizInstance> | null = null

async function loadViz(): Promise<VizInstance> {
  if (vizPromise === null) {
    vizPromise = import('@viz-js/viz').then(async (mod) => mod.instance())
  }
  return vizPromise
}

/** 把 Graphviz 输出的 SVG 规整成站点能直接用的形式 */
function normalizeSvg(raw: string): string {
  // Graphviz 会输出 XML 声明和 DOCTYPE,innerHTML 里不需要(还会带来解析噪声)
  let svg = raw
    .replace(/^<\?xml[^>]*\?>\s*/i, '')
    .replace(/<!DOCTYPE[^>]*>\s*/i, '')
    .trim()

  // 背景 polygon 是白底:纸墨主题下要透明,否则深色/宣纸底上出现一块白方块
  svg = svg.replace(/<polygon fill="white"[^>]*\/>/i, '')

  // pt → px(**不能把宽高直接删掉**,那是踩过的坑):
  //
  // 删掉 width/height 后 svg 失去固有尺寸,容器 CSS 的 `width:100%` 会把 viewBox
  // (335pt 宽)拉伸到容器宽度(760px)= **放大 2.27 倍**,14pt 的字渲染成 32px,
  // 图直接占满整屏(主人 2026-11-27 现场)。
  //
  // 正确做法是换算成 px 并**保留**固有尺寸:1pt = 4/3 px(Graphviz 按 72dpi 排版,
  // 浏览器 96dpi)。这样字号回到设计大小,CSS 再用 max-width:100% 在窄容器里等比缩小。
  const PT_TO_PX = 4 / 3
  const toPx = (ptText: string): string => String(Math.round(Number(ptText) * PT_TO_PX))

  svg = svg.replace(
    /(<svg[^>]*?)\swidth="([\d.]+)pt"/,
    (_m, head: string, w: string) => `${head} width="${toPx(w)}"`,
  )
  svg = svg.replace(
    /(<svg[^>]*?)\sheight="([\d.]+)pt"/,
    (_m, head: string, h: string) => `${head} height="${toPx(h)}"`,
  )
  return svg
}

/** 一次渲染:返回可直接插入 DOM 的 svg 字符串。失败抛错,由调用方回退。 */
export async function renderDotToSvg(dot: string): Promise<string> {
  const viz = await loadViz()
  const raw = viz.renderString(dot, { format: 'svg', engine: 'dot' })
  return normalizeSvg(raw)
}
