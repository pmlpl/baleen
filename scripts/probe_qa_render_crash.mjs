// 探针：历史会话「点不开」渲染崩溃复现（2026-11-25）。
//
// 症状：点击侧栏历史会话（如 7095 这类 50 条压测消息），API 200 拿到数据但渲染抛
// "Cannot read properties of undefined (reading 'replace')"（renderInline）——
// Vue 渲染函数崩了，页面空白 = 主人看到的「历史对话点不开」。
//
// 本探针把 QaView 的渲染入口 renderMarkdown（frontend/src/utils/markdown.ts）
// esbuild 打包到 node，逐条喂真实消息（7095 全部 + 6802 对照），抓崩溃消息与完整堆栈。
//
// 跑法（baleen 根目录）：node scripts\probe_qa_render_crash.mjs
// 数据：先跑 psql 落 %TEMP%\qa_msgs_7095_6802.json（文件头注释里有生成命令）。
// 看到什么算对：列出崩溃消息 id + 完整 Error.stack；退出码 0=有崩溃消息被定位。

import { createRequire } from 'module'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const require = createRequire(import.meta.url)
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

const pnpmDir = path.join(ROOT, 'frontend/node_modules/.pnpm')
const esbuildDir = fs.readdirSync(pnpmDir).find((d) => d.startsWith('esbuild@'))
const esbuild = require(path.join(pnpmDir, esbuildDir, 'node_modules/esbuild/lib/main.js'))

const tmp = process.env.TEMP || '/tmp'
const bundle = path.join(tmp, 'markdown_bundle.cjs')
await esbuild.build({
  entryPoints: [path.join(ROOT, 'frontend/src/utils/markdown.ts')],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  outfile: bundle,
  logLevel: 'silent',
})

const { renderMarkdown } = require(bundle)

// ---------------------------------------------------------------- 回归护栏(自包含,必跑)
// 2026-11-25 根因:3b41e7a 给 OL 正则加编号捕获组时三处 listItem[1] 一刀切成 [2],
// 无序列表项(单捕获组)从此渲染即崩——任一 "- " 项让 QaView 整条渲染 TypeError。
let fails = 0
const builtin = [
  ['- 简单无序', '<ul><li>简单无序</li></ul>'],
  ['- a\n- b', '<ul><li>a</li><li>b</li></ul>'],
  ['1. one\n2. two', '<ol><li>one</li><li>two</li></ol>'],
  ['3. three\n4. four', '<ol start="3">'],
  ['- 顶层\n  - 嵌套', '<ul><li>顶层<ul><li>嵌套</li></ul></li></ul>'],
]
for (const [t, expect] of builtin) {
  try {
    const r = renderMarkdown(t).replace(/\n/g, '')
    const ok = r.includes(expect)
    if (!ok) fails += 1
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${JSON.stringify(t)} -> ${r.slice(0, 90)}`)
  } catch (e) {
    fails += 1
    console.log(`CRASH ${JSON.stringify(t)} ${e.stack.split('\n')[0]}`)
  }
}
if (fails > 0) {
  console.log(`内置回归 ${builtin.length - fails}/${builtin.length} 通过 —— 有 FAIL/CRASH`)
  process.exit(1)
}
console.log(`内置回归 ${builtin.length}/${builtin.length} PASS`)

// ---------------------------------------------------------------- 真实消息扫描(可选:DB dump 在才跑)

// 生成命令（Git Bash，baleen 根目录）：
//   docker compose exec db psql -U baleen -d baleen -t -A -c "SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT session_id, id, role, content FROM qa_messages WHERE session_id IN (7095, 6802) ORDER BY session_id, id) t;" > "$TEMP/qa_msgs_7095_6802.json"
const dataPath = path.join(tmp, 'qa_msgs_7095_6802.json')
if (!fs.existsSync(dataPath)) {
  console.log(`\n(真实消息 dump 不存在,跳过: ${dataPath})`)
  process.exit(0)
}
const msgs = JSON.parse(fs.readFileSync(dataPath, 'utf-8'))

let crashes = 0
for (const m of msgs) {
  try {
    renderMarkdown(m.content)
  } catch (e) {
    crashes += 1
    console.log(`\n=== 崩溃: session=${m.session_id} msg=${m.id} role=${m.role} ===`)
    // 前缀二分:逐行累积,找到第一个崩的前缀 = 肇事行(表格等结构需要上下文才崩)
    const lines = m.content.split('\n')
    let hit = -1
    for (let i = 0; i < lines.length; i++) {
      try {
        renderMarkdown(lines.slice(0, i + 1).join('\n'))
      } catch {
        hit = i
        break
      }
    }
    if (hit >= 0) {
      console.log(`肇事行(第 ${hit + 1} 行),上下文:`)
      for (let j = Math.max(0, hit - 3); j <= hit; j++) {
        console.log(`  ${j + 1}: ${JSON.stringify(lines[j])}`)
      }
    } else {
      console.log(`单行前缀都能渲染——需整篇上下文,内容前 200 字: ${m.content.slice(0, 200)}`)
    }
    console.log(`Error 第一行: ${e.stack.split('\n')[0]}`)
  }
}
console.log(`\n共 ${msgs.length} 条消息, 崩溃 ${crashes} 条`)
