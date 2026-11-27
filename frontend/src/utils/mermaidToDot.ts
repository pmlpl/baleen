/**
 * mermaid flowchart → Graphviz DOT 转换器。
 *
 * **为什么要有这个东西**(2026-11-27,主人现场两轮未修复):
 * mermaid 把节点尺寸交给**浏览器量文字**,同一个 `.mermaid` 块在主人机器上渲染成
 * 空白框/只有一个节点,在我这里(同版本、同代码、同内容)完全正常。两轮防护
 * (bbox 比例 → 屏幕像素 + 按内容归一化)都没能解决,因为差异发生在 mermaid
 * 量文字的那一步,**判据再准也只能事后补救**。
 *
 * Graphviz WASM(@viz-js/viz)的布局在 WASM 里算完,**完全不读浏览器测量结果**,
 * 所以那个差异源被从机制上排除掉了。实测:同一输入渲染 5 次尺寸完全一致。
 *
 * 设计取舍:
 * - 只支持 LLM 实际会写的那部分语法(flowchart TD/LR + 方括号/圆角/菱形/圆 + 带标签的箭头)。
 *   遇到不认识的语法**宁可抛错回退 mermaid**,也不要以图省事画错。
 * - 中文字宽**自己按 em 估算**并写进节点 `width`:WASM 里没有中文字体度量表,
 *   Graphviz 会把 19.4em 的中英混排算窄 18%,文字直接跑出框外(实测)。预量后实测无溢出。
 */

const NL = String.raw`\n`
const ESC = '\\'

const RANKDIR: Record<string, string> = {
  TD: 'TB',
  TB: 'TB',
  LR: 'LR',
  RL: 'RL',
  BT: 'BT',
}

/** 节点字号(pt)与边标签字号 —— 与前端正文比例一致 */
const NODE_FONT = 14
const EDGE_FONT = 12

/** Graphviz 的 margin="0.2,…" 两侧合计 28.8pt */
const MARGIN_PT = 28.8
/** 抵消「WASM 无中文字体度量」的固定安全余量(pt) */
const SAFETY_PT = 12

/**
 * 估算一行文字的宽度(以 em 计)。
 *
 * WASM 里的度量对中英混排系统性偏窄,所以这里自己算:CJK 全角 1.0em、
 * ASCII 0.62em(实测 YaHei 下英文比 0.55 更宽)。宁可略宽(留白)也不能溢出。
 */
export function textWidthEm(text: string): number {
  let em = 0
  for (const ch of text) {
    const code = ch.codePointAt(0) ?? 0
    const wide =
      (code >= 0x2e80 && code <= 0x9fff) || // CJK 部首/汉字
      (code >= 0xff00 && code <= 0xffef) || // 全角标点
      (code >= 0x3000 && code <= 0x303f) // CJK 标点
    em += wide ? 1.0 : 0.62
  }
  return em
}

/** 多行标签里最宽一行的宽度(inch,含 margin 与安全余量) */
function nodeWidthIn(label: string): number {
  const lines = label.split(NL)
  const maxEm = Math.max(...lines.map(textWidthEm))
  const widthPt = maxEm * NODE_FONT + MARGIN_PT + SAFETY_PT
  return Math.max(0.6, widthPt / 72)
}

interface ParsedNode {
  label: string
  shape: 'box' | 'round' | 'diamond' | 'ellipse'
}

interface ParsedEdge {
  from: string
  to: string
  label: string
}

/** 转义成 DOT 字符串字面量内部可用的形式 */
function escapeLabel(raw: string): string {
  return raw
    .replace(/<br\s*\/?>/gi, NL) // mermaid 的换行标记 → DOT 换行
    .replace(/"/g, `${ESC}"`)
}

const NODE_RE = /^([A-Za-z_][\w-]*)\s*(\(\([^)]*\)\)|\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?/
const ARROW_RE = /^\s*(?:-{2,3}>|==>|-\.->|-{2,3})\s*(?:\|([^|]*)\|)?\s*/
const SKIP_RE =
  /^(?:subgraph|end|style|classDef|class|linkStyle|click|direction|accTitle|accDescr)\b/

/**
 * 把一段 mermaid flowchart 源码转成 DOT。
 *
 * 抛错表示「这段语法我不支持」,调用方应回退到 mermaid 渲染(而不是给出错图)。
 */
export function mermaidFlowchartToDot(source: string): string {
  const header = /^\s*(?:flowchart|graph)\s+(TD|TB|LR|RL|BT)/i.exec(source)
  if (header === null) throw new Error('只支持 flowchart/graph 语法')
  const rankdir = RANKDIR[header[1].toUpperCase()] ?? 'TB'

  const nodes = new Map<string, ParsedNode>()
  const edges: ParsedEdge[] = []

  const register = (id: string, rawShape?: string): string => {
    if (rawShape === undefined) {
      if (!nodes.has(id)) nodes.set(id, { label: id, shape: 'box' })
      return id
    }
    let label = rawShape
    let shape: ParsedNode['shape'] = 'box'
    if (rawShape.startsWith('((')) {
      label = rawShape.slice(2, -2)
      shape = 'ellipse'
    } else if (rawShape.startsWith('{')) {
      label = rawShape.slice(1, -1)
      shape = 'diamond'
    } else if (rawShape.startsWith('(')) {
      label = rawShape.slice(1, -1)
      shape = 'round'
    } else {
      label = rawShape.slice(1, -1)
    }
    nodes.set(id, { label: escapeLabel(label), shape })
    return id
  }

  for (const line of source.split('\n')) {
    const text = line.trim()
    if (text === '' || /^(?:flowchart|graph)\b/i.test(text) || text.startsWith('%%')) continue
    if (SKIP_RE.test(text)) continue

    let rest = text
    let prev: string | null = null
    let pendingLabel = ''
    let guard = 0

    while (rest.length > 0 && guard < 500) {
      guard += 1
      const nodeMatch = NODE_RE.exec(rest)
      if (nodeMatch === null) break
      const id = register(nodeMatch[1], nodeMatch[2])
      if (prev !== null) edges.push({ from: prev, to: id, label: pendingLabel })
      prev = id
      rest = rest.slice(nodeMatch[0].length)

      const arrowMatch = ARROW_RE.exec(rest)
      if (arrowMatch === null) break
      pendingLabel = arrowMatch[1] === undefined ? '' : escapeLabel(arrowMatch[1])
      rest = rest.slice(arrowMatch[0].length)
    }
  }

  if (nodes.size === 0) throw new Error('没有解析出任何节点')
  // 节点定义与边分开写:DOT 里先声明节点再连边,布局更稳
  const lines: string[] = [
    'digraph G {',
    `  rankdir=${rankdir};`,
    '  bgcolor="transparent";',
    '  nodesep=0.35;',
    '  ranksep=0.45;',
    `  node [shape=box, fontname="Microsoft YaHei", fontsize=${NODE_FONT}, margin="0.2,0.08"];`,
    `  edge [fontname="Microsoft YaHei", fontsize=${EDGE_FONT}, arrowsize=0.7];`,
  ]

  for (const [id, node] of nodes) {
    if (node.shape === 'diamond') {
      // 菱形内接矩形的可用宽度只有外框一半左右,所以按文字宽度 ×1.6 给外框
      const width = Math.max(1.6, nodeWidthIn(node.label) * 1.6)
      lines.push(
        `  ${id} [label="${node.label}", shape=diamond, width=${width.toFixed(3)}, height=${(width * 0.55).toFixed(3)}];`,
      )
    } else {
      const attrs = [`label="${node.label}"`, `width=${nodeWidthIn(node.label).toFixed(3)}`]
      if (node.shape === 'round') attrs.push('style="rounded"')
      else if (node.shape === 'ellipse') attrs.push('shape=ellipse')
      lines.push(`  ${id} [${attrs.join(', ')}];`)
    }
  }

  for (const edge of edges) {
    lines.push(
      edge.label === ''
        ? `  ${edge.from} -> ${edge.to};`
        : `  ${edge.from} -> ${edge.to} [label="${edge.label}"];`,
    )
  }

  lines.push('}')
  return lines.join('\n')
}

/** 这段源码看起来是不是我们能转的 flowchart(不解析,只做粗筛) */
export function looksLikeFlowchart(source: string): boolean {
  return /^\s*(?:flowchart|graph)\s+(?:TD|TB|LR|RL|BT)\b/i.test(source)
}
