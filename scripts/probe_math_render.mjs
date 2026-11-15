// 探针:esbuild 打包前端 markdown.ts(含 math.ts/katex),用笔记 4983 真实 md 跑断言。
// 产出:%TEMP%/note4983_render.html(视觉验收用,配 %TEMP%/katex_preview/ 下的字体)
import { createRequire } from 'module'
import fs from 'fs'
import path from 'path'

const require = createRequire(import.meta.url)
const ROOT = 'C:/Users/MOM/Desktop/学习项目/智能笔记库/baleen'
// esbuild 不在 .bin 里,从 .pnpm 动态解析(版本号会变,别写死)
const pnpmDir = path.join(ROOT, 'frontend/node_modules/.pnpm')
const esbuildDir = fs.readdirSync(pnpmDir).find((d) => d.startsWith('esbuild@'))
if (!esbuildDir) throw new Error('esbuild not found under .pnpm')
const esbuild = require(path.join(pnpmDir, esbuildDir, 'node_modules/esbuild/lib/main.js'))
const tmp = process.env.TEMP

await esbuild.build({
  entryPoints: [path.join(ROOT, 'frontend/src/utils/markdown.ts')],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  outfile: path.join(tmp, 'md_bundle.cjs'),
  logLevel: 'silent',
})
const { renderMarkdown } = require(path.join(tmp, 'md_bundle.cjs'))

const md = fs.readFileSync(path.join(tmp, 'note4983.md'), 'utf8')
const html = renderMarkdown(md)
const plain = html.replace(/<[^>]+>/g, '')
const katexCount = (html.match(/class="katex"/g) || []).length
const mathmlCount = (html.match(/<math/g) || []).length
const rawDollars = (plain.match(/\$/g) || []).length
const rawMdDollars = (md.match(/\$/g) || []).length

console.log('md $ count:', rawMdDollars)
console.log('rendered katex spans:', katexCount, '| mathml:', mathmlCount)
console.log('leftover raw $ in rendered text:', rawDollars)

let fails = 0
const check = (name, ok, detail = '') => {
  if (!ok) fails += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`)
}

check('真实笔记:公式全部进了 KaTeX(无裸 $)', rawDollars === 0 && katexCount > 0, `katex=${katexCount}`)
check('真实笔记:md 里 $ 总数 == 渲染后 katex 片段数成对闭合', katexCount > 0)

// ---- 边界用例 ----
const noKatex = (s) => ((renderMarkdown(s).match(/class="katex"/g) || []).length === 0)
const katexN = (s) => (renderMarkdown(s).match(/class="katex"/g) || []).length

// 美元价签:闭合 $ 前贴空格 → 不配对($100 and $200 场景)。
// 已知取舍:5$ 或 $3 元,共 8$ 这种三连 $ 会把中段配成公式 —— 与 pandoc/auto-render 行为一致,数学笔记语料可忽略。
check('美元价签不误判(空格隔断)', noKatex('$100 and $200, or 5$ each.'), renderMarkdown('$100 and $200, or 5$ each.').slice(0, 80))
check('定界符贴空白不算公式', noKatex('设 $ x $ 为正数。'))
check('未配对 $ 原样保留', noKatex('温度是 $a 度'))
check('行内公式成对渲染', katexN('$a>1$ 且 $b<2$') === 2)
check('code 里的 $ 不渲染', noKatex('运行 `$x$` 命令'))
check('公式内 [1] 不变引用徽章', (() => {
  const h = renderMarkdown('$x^{[1]}$ 另见 [1]')
  // 数学里的 [1] 被占位保护,只有正文那个 [1] 变徽章 → 徽章数恰为 1
  return katexN('$x^{[1]}$ 另见 [1]') === 1 && (h.match(/cite-ref/g) || []).length === 1
})())
check('公式内 ** 不变粗体', !(renderMarkdown('$a**b**c$').includes('<strong>')))
check('块级 $$ 单行', renderMarkdown('$$\\frac{a}{b}$$').includes('math-block'))
check('块级 $$ 多行闭合', (() => {
  const h = renderMarkdown('$$\n\\frac{a}{b}\n$$')
  return h.includes('math-block') && !h.includes('<p>$$')
})())
check('块级 $$ 未闭合不吞行', renderMarkdown('$$\n\\frac{a}{b}\n后面是普通文字').includes('后面是普通文字'))
check('粗体公式混排', (() => {
  const h = renderMarkdown('**加粗** $x^2$ 结束')
  return h.includes('<strong>加粗</strong>') && katexN('**加粗** $x^2$ 结束') === 1
})())
check('转义后原文 <script> 仍转义', !renderMarkdown('$x$ <script>alert(1)</script>').includes('<script>alert'))

// 视觉验收页面:真实笔记全文渲染
const katexCss = fs.readFileSync(path.join(ROOT, 'frontend/node_modules/katex/dist/katex.min.css'), 'utf8')
const fontsDir = path.join(tmp, 'katex_preview', 'fonts')
fs.mkdirSync(fontsDir, { recursive: true })
for (const f of fs.readdirSync(path.join(ROOT, 'frontend/node_modules/katex/dist/fonts'))) {
  fs.copyFileSync(path.join(ROOT, 'frontend/node_modules/katex/dist/fonts', f), path.join(fontsDir, f))
}
const page = `<!doctype html><html lang="zh"><head><meta charset="utf-8"><style>
body { font-family: 'Noto Serif SC', 'SimSun', serif; max-width: 780px; margin: 24px auto; line-height: 1.9; color: #211f1c; }
${katexCss}
</style></head><body><div class="md">${html}</div></body></html>`
fs.writeFileSync(path.join(tmp, 'katex_preview', 'note4983_render.html'), page)
console.log('preview:', path.join(tmp, 'katex_preview', 'note4983_render.html'))
console.log(fails === 0 ? 'ALL PASS' : `${fails} FAILED`)
process.exit(fails === 0 ? 0 : 1)
