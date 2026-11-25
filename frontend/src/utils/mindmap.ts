/**
 * 思维导图渲染:把 ```mindmap 围栏里的 **markdown 嵌套列表**渲染成交互式导图。
 *
 * 为什么不用 mermaid 的 mindmap:
 * - 它是实验特性,零交互(不能缩放/拖拽/折叠),形状只是 flowchart 的子集;
 * - 缩进语法对模型不友好(多一层空格就换父节点),长中文标签在 neutral 主题下
 *   还会被挤成单色一团。
 *
 * 改走 markmap:模型只需要输出最熟的 markdown 列表(`- ` / `  - ` 缩进),
 * 渲染端自带 fold / zoom / pan、节点支持粗体/链接/代码。我们只负责配色对齐纸墨。
 *
 * 体积纪律:和 mermaid 一样懒加载 —— 用户真的看到导图才下载
 * (@markmap/parse + render + view 合计约 154KB 源码)。
 */

type MarkmapInstance = import('@markmap/view').Markmap
/** 树的类型从 parse() 的返回值推导 —— @markmap/core 是传递依赖,
 *  没提升到 node_modules 顶层,不能直接 import type 它 */
type MarkmapTree = ReturnType<typeof import('@markmap/parse').parse>['root']

interface MarkmapBundle {
  parse: typeof import('@markmap/parse').parse
  Markmap: typeof import('@markmap/view').Markmap
}

let bundlePromise: Promise<MarkmapBundle> | null = null

async function loadMarkmap(): Promise<MarkmapBundle> {
  if (bundlePromise === null) {
    bundlePromise = Promise.all([import('@markmap/parse'), import('@markmap/view')]).then(
      ([parseMod, viewMod]) => ({ parse: parseMod.parse, Markmap: viewMod.Markmap }),
    )
  }
  return bundlePromise
}

/** 已挂到 DOM 上的导图实例(卸载时 destroy,避免 d3 监听泄漏) */
const instances = new WeakMap<Element, MarkmapInstance>()

/**
 * 纸墨配色。
 *
 * markmap 的默认主题是通用彩色(蓝紫橙),放在宣纸底上很跳。这里覆盖它的 CSS 变量:
 * 连线用淡墨、节点文字用浓墨、当前节点用朱砂。变量名是 markmap v1 的 `--mm-*` 契约,
 * 写在导图容器上即可 —— 不影响站内其他部分。
 */
const PAPER_INK_TOKENS = `
  --mm-line: var(--line-strong);
  --mm-line-width: 1.5px;
  --mm-line-opacity: 1;
  --mm-node-text: var(--ink);
  --mm-node-bg: var(--panel);
  --mm-node-padding: 3px 8px;
  --mm-node-radius: 6px;
  --mm-node-font-size: 14px;
  --mm-node-current-text: var(--seal);
  --mm-node-current-bg: var(--seal-soft);
  --mm-diagram-bg: transparent;
`

/** 判断围栏内容像不像 markdown 列表(至少一行 `- ` / `* ` / `+ `) */
function looksLikeMindmapList(code: string): boolean {
  return /^\s*[-*+] +\S/m.test(code)
}

/**
 * 渲染 root 下所有 ```mindmap 围栏。
 *
 * 目标节点:`<pre data-lang="mindmap"><code>- 根\n  - 子…</code></pre>`
 * (section.ts 会带 data-lang;markdown.ts 也补了 —— 见两个渲染器)
 *
 * 失败降级:保留原列表 + 一条「导图渲染失败」角标,绝不抛错让整页挂。
 */
export async function renderMindmapsIn(root: HTMLElement): Promise<void> {
  const targets = Array.from(root.querySelectorAll<HTMLPreElement>('pre')).filter((pre) => {
    if (pre.dataset.mindmapDone === '1') return false
    if (pre.dataset.lang !== 'mindmap') return false
    const code = pre.querySelector('code')
    return code !== null && looksLikeMindmapList(code.textContent ?? '')
  })
  if (targets.length === 0) return

  const { parse, Markmap } = await loadMarkmap()

  for (const pre of targets) {
    const code = pre.querySelector('code')?.textContent ?? ''
    pre.dataset.mindmapDone = '1'
    try {
      const { root: tree } = parse(code)
      const figure = document.createElement('div')
      figure.className = 'mindmap-figure'
      figure.style.cssText = PAPER_INK_TOKENS

      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
      // 容器必须有确定高度:markmap 自己量 svg 尺寸布局
      svg.setAttribute('class', 'mindmap-svg')
      figure.appendChild(svg)
      pre.replaceWith(figure)

      const mm = Markmap.create(svg, tree as MarkmapTree, {
        // 初始展开两层:根 + 第一圈,再深就自己点开(避免一上来糊成一团)
        foldLevel: 2,
        duration: 200,
        pan: true,
        zoom: true,
        fitPadding: 12,
        maxScale: 3,
        minScale: 0.3,
      })
      instances.set(figure, mm)
      // 首次 fit 要等容器有尺寸;create 内部会在尺寸就绪时自动 fit
      mm.fit()
    } catch {
      pre.classList.add('mindmap-error')
      if (pre.querySelector('.mindmap-error-badge') === null) {
        const badge = document.createElement('span')
        badge.className = 'mindmap-error-badge'
        badge.textContent = '导图渲染失败,按列表显示'
        pre.prepend(badge)
      }
    }
  }
}

/** 页面卸载时销毁实例(keep-alive 切页时可调用;元素消失后 d3 监听会自然失效) */
export function destroyMindmapsIn(root: HTMLElement): void {
  for (const figure of Array.from(root.querySelectorAll('.mindmap-figure'))) {
    instances.get(figure)?.destroy()
    instances.delete(figure)
  }
}
