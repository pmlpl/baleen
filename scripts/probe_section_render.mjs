// 探针:section.ts 的父块展示渲染回归（2026-11-19 六连修 P0 的回归护栏）。
//
// 背景：2026-11-19 主人复核 7 条视频笔记，抓出「代码显示成纯文本 / 列表和正文一样」
// —— 根因是展示链路复用了检索的纯文本序列化（chunker._block_text）。当天后端补了
// _block_display_text（围栏/列表标记/引用前缀/粗体行内代码斜体），前端 section.ts
// 补了四种渲染分支。**这批改动此前没有任何自动化回归**，靠人工截图验证。
//
// 本探针把六个分支钉住：围栏代码块 / 无序列表 / 有序列表 / 引用块 / 行内粗体 /
// 行内代码。另加易误伤的边界用例（算式斜体、连字符、单行表格、未闭合围栏）。
//
// 跑法（PowerShell，在 baleen 根目录）：
//   node scripts\probe_section_render.mjs
// 看到什么算对：最后一行 `N/N PASS`，退出码 0。有 FAIL 会打印具体差异。
//
// 注意：这是「一次性探针」的正式化，不是 vitest —— frontend 没装测试框架
// （package.json 只有 vue-tsc/vite），引入 vitest 要动构建链。用 esbuild 打包
// 纯函数到 node 里跑是 math.ts 探针已验证的零依赖范式。

import { createRequire } from 'module'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const require = createRequire(import.meta.url)
// 用 fileURLToPath 而不是手拼 URL.pathname —— 路径含中文时 pathname 是
// percent-encoded(%E5%AD%A6...)，fs 会找不到目录（math 探针写死了绝对路径躲过了这个坑）
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

// esbuild 不在 .bin 里,从 .pnpm 动态解析(版本号会变,别写死) —— 与 probe_math_render.mjs 同款
const pnpmDir = path.join(ROOT, 'frontend/node_modules/.pnpm')
if (!fs.existsSync(pnpmDir)) {
  console.error(`[error] 找不到 ${pnpmDir}\n请先在 baleen\\frontend 跑 pnpm install`)
  process.exit(2)
}
const esbuildDir = fs.readdirSync(pnpmDir).find((d) => d.startsWith('esbuild@'))
if (!esbuildDir) {
  console.error('[error] 找不到 esbuild(@.pnpm 下)。请先在 baleen\\frontend 跑 pnpm install')
  process.exit(2)
}
const esbuild = require(path.join(pnpmDir, esbuildDir, 'node_modules/esbuild/lib/main.js'))

const tmp = process.env.TEMP || '/tmp'
const bundle = path.join(tmp, 'section_bundle.cjs')

await esbuild.build({
  entryPoints: [path.join(ROOT, 'frontend/src/utils/section.ts')],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  outfile: bundle,
  logLevel: 'silent',
})

const { renderSectionText } = require(bundle)

// ---------------------------------------------------------------- 断言框架

let fails = 0
const check = (name, ok, detail = '') => {
  if (!ok) fails += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail && !ok ? '\n      ' + detail : ''}`)
}

/** 渲染一段父块文本（不带图片、不带引用目标）。 */
const render = (raw) => renderSectionText(raw)

/** 把 HTML 压成便于比对的单行摘要。 */
const brief = (html) => html.replace(/\n/g, '\\n').slice(0, 160)

// ================================================================
// 一、围栏代码块（11-19 P0 分支①）
// 主人原话：「代码显示成纯文本」。6009 有 17 个 codeBlock 但有 0 个围栏标记。
// ================================================================

{
  const h = render('```bash\ndocker ps -a\n```')
  check(
    '围栏: ```bash 渲染成 pre.sec-code 且带 data-lang',
    h.includes('<pre class="sec-code" data-lang="bash">') && h.includes('docker ps -a'),
    brief(h),
  )
  check('围栏: 代码内容被转义（< > 不出现在 HTML 标签位）', (() => {
    const x = render('```html\n<script>alert(1)</script>\n```')
    return x.includes('&lt;script&gt;') && !x.includes('<script>alert')
  })(), brief(h))
  check('围栏: 没有 data-lang 的空围栏也成立（不要求语言）', (() => {
    const x = render('```\nplain text line\n```')
    return x.includes('<pre class="sec-code">') && x.includes('plain text line')
  })())
  check('围栏: 代码里的 ** 不被渲染成粗体（代码优先于行内 md）', (() => {
    const x = render('```python\nx = a ** 2\n```')
    return !x.includes('<strong>') && x.includes('a ** 2')
  })())
  check('围栏: 未闭合也吃到结尾，不把后续正文吞成代码', (() => {
    const x = render('```bash\ndocker ps\n后面这段是普通说明文字')
    // 未闭合围栏按设计吃到结尾（原样保留代码），但绝不能把文字渲染成 <p> 之外的怪东西
    return x.includes('<pre class="sec-code"') && x.includes('后面这段是普通说明文字')
  })())
  check('围栏: 围栏之间的正文不被当成代码', (() => {
    const x = render('前文段落\n\n```bash\nls\n```\n\n后文段落')
    return x.includes('<p>前文段落</p>') && x.includes('<p>后文段落</p>')
  })(), brief(render('前文段落\n\n```bash\nls\n```\n\n后文段落')))
  check('围栏: mermaid 围栏与普通围栏同形（走 pre.code,前端按内容识别）', (() => {
    const x = render('```mermaid\ngraph TD\nA-->B\n```')
    return x.includes('data-lang="mermaid"') && x.includes('A--&gt;B')
  })())
}

// ================================================================
// 二、无序列表（分支②）  主人原话：「列表和正文一样」
// ================================================================

{
  const h = render('- 第一项\n- 第二项\n- 第三项')
  check('无序列表: 渲染成 ul.sec-list 含三个 li', (() => {
    const n = (h.match(/<li>/g) || []).length
    return h.includes('<ul class="sec-list">') && n === 3
  })(), brief(h))
  check('无序列表: 列表项内支持行内粗体', render('- **重点** 内容').includes('<strong>重点</strong>'))
  check('无序列表: 连字符不误伤（「3-4 个」不是列表）', (() => {
    const x = render('大约 3-4 个步骤')
    return !x.includes('sec-list') && x.includes('3-4 个')
  })(), brief(render('大约 3-4 个步骤')))
  check('无序列表: 两格缩进续行并入上一条（不拆成新 li）', (() => {
    const x = render('- 第一项\n  续行内容\n- 第二项')
    // 续行以 <br> 接在前一项后面:第一项<br>续行内容
    return (x.match(/<li>/g) || []).length === 2 && x.includes('第一项<br>续行内容')
  })(), brief(render('- 第一项\n  续行内容\n- 第二项')))
}

// ================================================================
// 三、有序列表（分支③）
// ================================================================

{
  const h = render('1. 步骤一\n2. 步骤二\n3. 步骤三')
  check('有序列表: 渲染成 ol.sec-list（不是 ul）', (() => {
    const n = (h.match(/<li>/g) || []).length
    return h.includes('<ol class="sec-list">') && !h.includes('<ul class="sec-list">') && n === 3
  })(), brief(h))
  check('有序列表: 数字序号本身不进正文（只留 li 内容）', !render('1. 步骤一').includes('1. 步骤一'))
}

// ================================================================
// 四、引用块（分支④）
// ================================================================

{
  const h = render('> 这是一段引用\n> 第二行')
  check('引用块: 渲染成 blockquote.sec-quote', h.includes('<blockquote class="sec-quote">'), brief(h))
  check('引用块: 内部内容递归渲染（引用里的列表仍成列表）', (() => {
    const x = render('> - 引用内列表项')
    return x.includes('<blockquote') && x.includes('sec-list')
  })(), brief(render('> - 引用内列表项')))
  check('引用块: 引用结束后的正文回到普通段落', (() => {
    const x = render('> 引用内容\n\n普通段落')
    return x.includes('</blockquote>') && x.includes('<p>普通段落</p>')
  })(), brief(render('> 引用内容\n\n普通段落')))
}

// ================================================================
// 五、行内粗体（分支⑤）
// ================================================================

{
  check('行内: **粗体** 渲染成 strong', render('这里是 **重点内容** 结束').includes('<strong>重点内容</strong>'))
  // ⚠️ 2026-11-19 探针发现真缺陷:以 ** 开头的行曾被 looksLikeCode 判成代码
  // （注释规则 `/^\s*(\/\/|\/\*|\*)/` 把任何 * 开头都算注释），整段被吞进 <pre>。
  // 下面三条钉住修复：粗体开头的段落必须仍是段落。
  check('行内: 整行就是一个粗体段（**x** 独占一行）不被判成代码', (() => {
    const x = render('**重点内容**')
    return !x.includes('<pre') && x.includes('<strong>重点内容</strong>')
  })(), brief(render('**重点内容**')))
  check('行内: 以粗体开头 + 后接文字也不被判成代码', (() => {
    const x = render('**重点内容** 后面还有字')
    return !x.includes('<pre') && x.includes('<strong>重点内容</strong>') && x.includes('后面还有字')
  })(), brief(render('**重点内容** 后面还有字')))
  check('注释: 多行注释续行（* 单个星号 + 空格）仍识别为代码', (() => {
    const x = render(' * 这是 C 风格注释的续行')
    return x.includes('<pre class="sec-code">')
  })(), brief(render(' * 这是 C 风格注释的续行')))
  check('行内: 单个 ** 不成对时不误渲染', (() => {
    const x = render('这里有 ** 没闭合')
    return !x.includes('<strong>')
  })(), brief(render('这里有 ** 没闭合')))
  check('行内: 粗体内可含空格与标点', (() => {
    const x = render('**注意 这里 有空格**')
    return x.includes('<strong>注意 这里 有空格</strong>')
  })(), brief(render('**注意 这里 有空格**')))
}

// ================================================================
// 六、行内代码（分支⑥）
// ================================================================

{
  check('行内: `code` 渲染成 code.sec-code-inline', (() => {
    const x = render('运行 `docker ps` 命令')
    return x.includes('<code class="sec-code-inline">docker ps</code>')
  })(), brief(render('运行 `docker ps` 命令')))
  check('行内: 行内代码内容被转义', (() => {
    const x = render('用 `<div>` 标签')
    return x.includes('&lt;div&gt;') && !x.includes('<div>')
  })(), brief(render('用 `<div>` 标签')))
  check('行内: 单反引号不闭合时不误渲染', !render('这里是 ` 没闭合').includes('sec-code-inline'))
}

// ================================================================
// 七、斜体（保守匹配，防算式误伤）
// ================================================================

{
  const h = render('这是 *强调* 文字')
  check('斜体: *强调* 渲染成 em', h.includes('<em>强调</em>'), brief(h))
  check('斜体: 算式「3 * 4 * 5」不被误渲染（11-19 明确要求）', (() => {
    const x = render('计算 3 * 4 * 5 的结果')
    return !x.includes('<em>')
  })(), brief(render('计算 3 * 4 * 5 的结果')))
  check('斜体: 变量式 a * b 不被误渲染', !render('设 a * b 为乘积').includes('<em>'))
}

// ================================================================
// 八、老笔记兼容（关键回归风险：这些分支不能影响存量笔记）
// ================================================================

{
  check('兼容: 纯文字段落行为不变', render('这是一段普通文字。') === '<p>这是一段普通文字。</p>')
  check('兼容: 段内换行保留为 <br>', (() => {
    const x = render('第一行\n第二行')
    return x === '<p>第一行<br>第二行</p>'
  })(), brief(render('第一行\n第二行')))
  check('兼容: 空白被裁掉（前后空行不生成空段落）', render('\n\n正文\n\n') === '<p>正文</p>')
  check('兼容: 单行「a | b」不被误判成表格', (() => {
    const x = render('结果 a | b 这样')
    return !x.includes('<table') && x.includes('a | b')
  })(), brief(render('结果 a | b 这样')))
}

// ================================================================
// 九、XSS 底线（模型输出不可信）
// ================================================================

{
  check('安全: 正文里的 <script> 被转义', (() => {
    const x = render('<script>alert(1)</script>')
    return !x.includes('<script>') && x.includes('&lt;script&gt;')
  })())
  check('安全: 列表项里的 <img onerror> 被转义', (() => {
    const x = render('- <img src=x onerror=alert(1)>')
    return !x.includes('<img') && x.includes('&lt;img')
  })())
  check('安全: 粗体内容里的标签被转义', (() => {
    const x = render('**<b>x</b>**')
    return !x.includes('<b>x</b>')
  })())
}

// ---------------------------------------------------------------- 汇总

console.log('')
if (fails === 0) {
  console.log('全部通过')
} else {
  console.log(`有 ${fails} 条失败`)
}
process.exit(fails === 0 ? 0 : 1)
