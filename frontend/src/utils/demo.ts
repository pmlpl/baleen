/**
 * ```demo 围栏渲染:理科交互演示(模型生成的自包含 HTML)进沙箱 iframe。
 *
 * 沙箱配方(2026-11-28,调研 Claude Artifacts / MicroSims / OSP 主提示词后定稿):
 * - sandbox="allow-scripts" 且**不给 allow-same-origin** → 不透明源:
 *   脚本能跑,但摸不到父页 DOM/cookie/localStorage,模型输出不可信的代码出不了沙箱;
 * - srcdoc 里注入 CSP:default-src 'none'(网络全禁) + script/style 'unsafe-inline'
 *   (内联脚本样式放行,演示才有交互) + img/media/font 走 data:/blob:;
 *   注意 srcdoc 文档是不透明源,CSP 里 'self' 匹配不到任何东西,必须逐项写;
 * - 沙箱 iframe 的 contentDocument 父页读不到,高度协商走 postMessage 单向通道
 *   (demo 内的引导脚本向父页报高度,pym.js 同款;这是沙箱唯一的口子,
 *   父页按 event.source 对号入座,伪造消息对不上帧就被丢弃)。
 *
 * 截断/非 HTML 的围栏不渲染(与 svg.ts 的角标退化同款):缺 </script> = 输出被
 * max_tokens 腰斩,补全也救不回坏掉的 JS,按代码显示。
 */

const CSP_META =
  '<meta http-equiv="Content-Security-Policy" content=' +
  '"default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; ' +
  'img-src data: blob:; media-src data: blob:; font-src data:;">'

const MIN_HEIGHT = 240
const MAX_HEIGHT = 600
const START_HEIGHT = 450

/** demo 内嵌的高度引导脚本:load/双延时/ResizeObserver 三路都会量一次并上报 */
const HEIGHT_BOOTSTRAP = `
<script>
(function(){
  function __bdh(){
    var h = Math.max(
      document.documentElement.getBoundingClientRect().height,
      document.body ? document.body.scrollHeight : 0
    );
    parent.postMessage({ type: 'baleen-demo-height', height: Math.ceil(h) }, '*');
  }
  window.addEventListener('load', function(){ __bdh(); setTimeout(__bdh, 300); setTimeout(__bdh, 1200); });
  if (window.ResizeObserver) {
    var ro = new ResizeObserver(__bdh);
    ro.observe(document.documentElement);
    if (document.body) ro.observe(document.body);
  }
})();
</script>`

const liveFrames = new WeakSet<Window>()
let messageBound = false

function bindHeightListener(): void {
  if (messageBound) return
  messageBound = true
  window.addEventListener('message', (event: MessageEvent) => {
    const data = event.data as { type?: string; height?: number } | null
    if (data === null || typeof data !== 'object' || data.type !== 'baleen-demo-height') return
    if (!liveFrames.has(event.source as Window)) return // 不是我们渲染的帧,忽略
    const height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(data.height ?? 0)))
    for (const frame of document.querySelectorAll<HTMLIFrameElement>('iframe.demo-frame')) {
      if (frame.contentWindow === event.source) {
        frame.style.height = `${height}px`
        return
      }
    }
  })
}

function isTruncated(source: string): boolean {
  const hasScript = /<script\b/i.test(source)
  const closedScript = /<\/script\s*>/i.test(source)
  return hasScript && !closedScript
}

function looksLikeHtml(source: string): boolean {
  return /<(!doctype|html|head|body|script|canvas|svg|style|div)\b/i.test(source)
}

/** 组装 srcdoc:完整文档就原地注入 CSP+引导脚本;碎片就包一层样板壳 */
function buildSrcdoc(source: string): string {
  if (/<html[\s>]/i.test(source)) {
    let doc = /<head[^>]*>/i.test(source)
      ? source.replace(/<head([^>]*)>/i, `<head$1>${CSP_META}`)
      : source.replace(/<html([^>]*)>/i, `<html$1><head>${CSP_META}</head>`)
    doc = /<\/body\s*>/i.test(doc)
      ? doc.replace(/<\/body\s*>/i, `${HEIGHT_BOOTSTRAP}</body>`)
      : `${doc}${HEIGHT_BOOTSTRAP}`
    return doc
  }
  return (
    `<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">` +
    `<meta name="viewport" content="width=device-width, initial-scale=1">` +
    `${CSP_META}<style>html,body{margin:0;padding:8px;font-family:serif;}` +
    `button,select,input{font:inherit;}</style></head><body>` +
    `${source}${HEIGHT_BOOTSTRAP}</body></html>`
  )
}

function addBadge(pre: HTMLPreElement, text: string): void {
  pre.classList.add('svg-error') // 复用同一套角标样式
  if (pre.querySelector('.svg-error-badge') === null) {
    const badge = document.createElement('span')
    badge.className = 'svg-error-badge'
    badge.textContent = text
    pre.prepend(badge)
  }
}

/** 把一个 pre[data-lang="demo"] 换成沙箱 iframe;幂等(dataset.demoDone) */
function renderOne(pre: HTMLPreElement): void {
  const codeEl = pre.querySelector('code')
  const source = (codeEl?.textContent ?? '').trim()
  pre.dataset.demoDone = '1'
  if (!looksLikeHtml(source)) {
    addBadge(pre, 'demo 内容不是 HTML,按代码显示')
    return
  }
  if (isTruncated(source)) {
    addBadge(pre, 'demo 没有生成完整(输出被截断),按代码显示')
    return
  }

  const figure = document.createElement('div')
  figure.className = 'demo-figure'
  const frame = document.createElement('iframe')
  frame.className = 'demo-frame'
  // allow-scripts 且刻意不给 allow-same-origin:不透明源,模型代码出不了沙箱
  frame.setAttribute('sandbox', 'allow-scripts')
  frame.style.height = `${START_HEIGHT}px`
  frame.srcdoc = buildSrcdoc(source)
  figure.appendChild(frame)
  pre.replaceWith(figure)
  // browsing context 在插入文档树时才创建,分离态 contentWindow 是 null,插入后再登记
  if (frame.contentWindow !== null) liveFrames.add(frame.contentWindow)
}

export async function renderDemosIn(root: HTMLElement): Promise<void> {
  bindHeightListener()
  // ```demo 之外,完整文档型 ```html 围栏(<!DOCTYPE/<html> + script/canvas/svg)也提升为
  // 沙箱演示 —— 模型偶尔不按围栏名出牌(Claude Artifacts 对 ```html 同样直接渲染);
  // 片段型 html 代码(教学示例等)保持代码块原样,不打扰。
  const targets = Array.from(
    root.querySelectorAll<HTMLPreElement>('pre[data-lang="demo"], pre[data-lang="html"]'),
  ).filter((pre) => pre.dataset.demoDone !== '1')
  for (const pre of targets) {
    const lang = (pre.getAttribute('data-lang') ?? '').toLowerCase()
    const source = (pre.querySelector('code')?.textContent ?? '').trim()
    if (lang !== 'demo') {
      // html 围栏只有「完整文档 + 有脚本/画布」才提升,否则留在代码块
      const fullDoc = /<(!DOCTYPE\s+html|html[\s>])/i.test(source)
      const hasEngine = /<(script|canvas|svg)\b/i.test(source)
      if (!fullDoc || !hasEngine || !looksLikeHtml(source)) {
        pre.dataset.demoDone = '1'
        continue
      }
    }
    renderOne(pre)
  }
}
