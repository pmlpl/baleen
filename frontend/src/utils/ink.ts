/**
 * 宣纸墨稿:用 rough.js 把 Graphviz 算好的几何**重描成手绘墨线**。
 *
 * **主人 2026-11-28 比稿拍板**(A+B 混用):线性流程走稿件步骤条,分支/菱形流程
 * 走这里的墨稿 —— 双线抖动墨框 + 宋体 + 朱砂起止 + 黛青判断,像宣纸上的手绘稿。
 * 坐标层仍然是 Graphviz WASM(确定性不变),只换笔画层;这也是 draw.io「rough 风格」
 * 和 mermaid v11 `look: handDrawn` 的同一思路。
 *
 * 关键实现细节(都在 design-lab 里踩过):
 * - Graphviz 的 svg 有根 <g transform="translate(...)"> 把负坐标平移进视野,
 *   重描时**必须带上**,否则全部笔画落到 viewBox 外被裁成空白;
 * - rough.js 的 rc.xxx 只返回节点、不自动插入 DOM,每个都要手动 append;
 * - 端点判定要**分开**统计入度/出度:出−入 合并算会把「2 出 1 入」的菱形
 *   误判成起点(degree=1 撞上"只出不进")。
 */

import { renderDotToSvg } from './viz'

const SVGNS = 'http://www.w3.org/2000/svg'

const INK = {
  stroke: '#5f594e', // 淡墨描边(节点框)
  edge: '#8f8677', // 更淡一档的墨(连线,不抢正文)
  paper: '#fffdf8', // 普通节点:宣纸面
  decision: '#e8eef2', // 菱形判断:黛青底
  endpoint: '#f7ebe8', // 起点/终点:朱砂纸
  text: '#211f1c',
}

/**
 * 对 renderDotToSvg 的输出做墨稿重描(原地替换 svg 内容)。
 * 输入的 svg 必须已含 Graphviz 几何(viewBox/width/height/根 g transform)。
 */
export async function inkifyGraphvizSvg(svgEl: SVGSVGElement): Promise<void> {
  const src = svgEl

  const out = document.createElementNS(SVGNS, 'svg')
  out.setAttribute('viewBox', src.getAttribute('viewBox') ?? '0 0 100 100')
  out.setAttribute('width', src.getAttribute('width') ?? '')
  out.setAttribute('height', src.getAttribute('height') ?? '')
  out.setAttribute('class', src.getAttribute('class') ?? '')

  // Graphviz 的根 <g> transform 必须带上(见模块注释)
  const rootG = document.createElementNS(SVGNS, 'g')
  const srcRoot = src.querySelector(':scope > g')
  if (srcRoot !== null) rootG.setAttribute('transform', srcRoot.getAttribute('transform') ?? '')
  out.appendChild(rootG)

  const roughMod = await import('roughjs')
  const rc = roughMod.default.svg(out)

  // 端点判定:起点=只出不进,终点=只进不出。分开统计(见模块注释的坑)
  const outdeg = new Map<string, number>()
  const indeg = new Map<string, number>()
  for (const g of src.querySelectorAll('g.edge')) {
    const t = (g.querySelector('title')?.textContent ?? '').trim()
    const m = /^(.+?)->(?:<.+?>)?(.+)$/.exec(t)
    if (m !== null) {
      outdeg.set(m[1], (outdeg.get(m[1]) ?? 0) + 1)
      indeg.set(m[2], (indeg.get(m[2]) ?? 0) + 1)
    }
  }

  // 墨稿文字:宋体,和站点正文同一支笔
  const styledText = (t: SVGElement, fill: string): SVGElement => {
    const c = t.cloneNode(true) as SVGElement
    c.setAttribute('fill', fill)
    c.setAttribute('font-family', 'Noto Serif SC, Songti SC, serif')
    return c
  }

  let seed = 11 // 固定起点:同一张图每次渲染笔画一致(确定性)
  const nodeWrap = (cls: string): SVGGElement => {
    const g = document.createElementNS(SVGNS, 'g')
    g.setAttribute('class', cls)
    rootG.appendChild(g)
    return g
  }

  // 先画边(在节点下层)
  for (const g of src.querySelectorAll('g.edge')) {
    const wrap = nodeWrap('edge')
    const p = g.querySelector('path')
    if (p !== null) {
      wrap.appendChild(
        rc.path(p.getAttribute('d') ?? '', {
          stroke: INK.edge,
          strokeWidth: 1.3,
          roughness: 1.9,
          bowing: 1.6,
          seed: seed++,
        }),
      )
    }
    const head = g.querySelector('polygon')
    if (head !== null) {
      const c = head.cloneNode(true) as SVGPolygonElement
      c.setAttribute('fill', INK.edge)
      c.setAttribute('stroke', INK.edge)
      wrap.appendChild(c)
    }
    for (const t of g.querySelectorAll('text')) wrap.appendChild(styledText(t, INK.stroke))
  }

  // 再画节点(盖住边的端头)
  for (const g of src.querySelectorAll('g.node')) {
    const wrap = nodeWrap('node')
    const id = (g.querySelector('title')?.textContent ?? '').trim()
    const od = outdeg.get(id) ?? 0
    const ig = indeg.get(id) ?? 0
    const isEndpoint = (od > 0 && ig === 0) || (ig > 0 && od === 0)

    const opts = {
      fill: isEndpoint ? INK.endpoint : INK.paper,
      fillStyle: 'solid' as const,
      stroke: INK.stroke,
      strokeWidth: 1.3,
      roughness: 1.7,
      bowing: 1.4,
      seed: seed++,
    }
    const poly = g.querySelector('polygon')
    const ell = g.querySelector('ellipse')
    if (poly !== null) {
      // rough.js 的 Point 是 [x, y] 二元组,这里显式收窄
      const pts = (poly.getAttribute('points') ?? '')
        .trim()
        .split(/\s+/)
        .map((pair): [number, number] => {
          const [x, y] = pair.split(',').map(Number)
          return [x, y]
        })
      // 菱形 = 4 个顶点 → 判断节点,给黛青底
      if (pts.length === 4) opts.fill = INK.decision
      wrap.appendChild(rc.polygon(pts, opts))
    } else if (ell !== null) {
      wrap.appendChild(
        rc.ellipse(
          Number(ell.getAttribute('cx')),
          Number(ell.getAttribute('cy')),
          Number(ell.getAttribute('rx')) * 2,
          Number(ell.getAttribute('ry')) * 2,
          opts,
        ),
      )
    }
    for (const t of g.querySelectorAll('text')) wrap.appendChild(styledText(t, INK.text))
  }

  // 原地替换内容:svgEl 的类名/尺寸留给 figure 的 CSS 用
  while (svgEl.firstChild !== null) svgEl.removeChild(svgEl.firstChild)
  while (out.firstChild !== null) svgEl.appendChild(out.firstChild)
}

export { renderDotToSvg }
