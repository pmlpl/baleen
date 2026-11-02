<script setup lang="ts">
/**
 * 笔记详情:整篇连续阅读 + 左侧锚点目录。
 *
 * 数据是 GET /api/notes/{id} 的 parents(父块真源)。所有父块按 ord 顺序**连续渲染**
 * 成一整篇,左侧目录只是锚点 —— 点目录滚动到对应位置,不是切换内容。
 *
 * 滚动时用 IntersectionObserver 反查「现在看的是哪一节」,左侧目录同步高亮,
 * 点过的锚点也能通过 URL 的 #hash 记住(刷新/分享能回到同一节)。
 *
 * markdown 有正文时不在这里渲染(父块才是真源);渲染器手写,原文先转义,
 * 笔记里写了 <script> 也只显示成文字。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import { deleteNote, getNote, updateNote } from '../api/notes'
import type { NoteDetail, NoteImage, NoteParent } from '../api/notes'
import { renderMermaidIn } from '../utils/mermaid'
import { renderSvgsIn } from '../utils/svg'

const props = defineProps<{ id: string }>()

const router = useRouter()

const note = ref<NoteDetail | null>(null)
const loading = ref(false)
const errorText = ref('')

/** ---- 编辑模式(用户自编辑) ---- */
const editing = ref(false)
const editTitle = ref('')
const editMarkdown = ref('')
/** 进入编辑时的快照:只提交真正改过的字段(含图笔记只改标题才不会撞 400) */
const editSnapshot = ref<{ title: string; markdown: string }>({ title: '', markdown: '' })
const saving = ref(false)
const saveError = ref('')
const savedHint = ref('')

/** 当前视口内的章节 id,用于目录高亮 */
const activeId = ref<number | null>(null)
/** 目录列表元素(高亮项自动滚进可视区用) */
const tocListEl = ref<HTMLElement | null>(null)
/** 左栏滚动容器(目录本身过高时在这里滚) */
const tocEl = ref<HTMLElement | null>(null)
/** 右栏滚动容器(正文在这里滚,不是整页滚) */
const contentEl = ref<HTMLElement | null>(null)

/** 目录条目:章节名 + 缩进层级 + 原块 */
interface TocEntry {
  parent: NoteParent
  label: string
  depth: number
}

/**
 * heading_path → 目录条目。
 * 「笔记标题 > 一、闭包 > 官方定义」→ label=「官方定义」、depth=1。
 * 只有笔记标题一层(整篇没分节)时 depth=0。
 */
const toc = computed<TocEntry[]>(() => {
  if (note.value === null) return []
  const title = note.value.title
  return note.value.parents.map((parent) => {
    const raw = (parent.heading_path ?? '').trim()
    if (raw === '') return { parent, label: '正文', depth: 0 }
    const parts = raw
      .split('>')
      .map((p) => p.trim())
      .filter((p) => p !== '')
    // 去掉开头与笔记标题相同的那一段(它只是根);标题可能被截断,容忍前缀关系
    if (parts.length > 1) {
      const head = parts[0] as string
      if (head === title || title.startsWith(head) || head.startsWith(title)) {
        parts.shift()
      }
    }
    if (parts.length === 0) return { parent, label: '正文', depth: 0 }
    return { parent, label: parts[parts.length - 1] as string, depth: parts.length - 1 }
  })
})

/** 每个父块 id → 它的排版后 HTML(按父块顺序消费图片清单,v16) */
const htmlById = computed<Map<number, string>>(() => {
  const map = new Map<number, string>()
  if (note.value === null) return map
  // 游标容器:父块按 ord 顺序渲染,图片清单也是文档顺序,顺序消费即可对上原位
  const cursor = { i: 0 }
  for (const parent of note.value.parents) {
    map.set(parent.chunk_id, renderSectionText(parent.text, note.value.images, cursor))
  }
  return map
})

/**
 * 父块原文 → 有排版的 HTML。
 *
 * 父块是「自然语言 + 裸代码」混排,没有 markdown 代码围栏,所以不能直接用
 * renderMarkdown。按启发式分块:代码特征行聚成代码块,其余聚成段落。
 * 全部先 HTML 转义,原文含 <script> 也只显示成文字。
 */
function renderSectionText(
  raw: string,
  images: NoteImage[] = [],
  cursor: { i: number } = { i: 0 },
): string {
  const escaped = (s: string): string =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

  /**
   * `[图片] alt` 标记 → 真实插图(chunker 给 image 块产的就是这行文本)。
   * 按文档顺序取下一张图;取完了就保留原标记文字,不假装有图。
   * media key 走 /api/media/,外链走 src 原值(可能加载不出来,给 alt 兜底)。
   */
  const renderImageMarker = (line: string): string | null => {
    if (!/^\s*\[图片\]\s*/.test(line)) return null
    const image = images[cursor.i]
    if (image === undefined) return null
    cursor.i += 1
    const url = image.media !== null ? `/api/media/${image.media}` : (image.src ?? '')
    const alt = escaped(image.alt === '' ? '笔记插图' : image.alt)
    if (url === '') {
      return `<p class="img-missing">[图片] ${alt}(原图未随笔记导入)</p>`
    }
    return (
      `<figure class="note-img"><img src="${escaped(url)}" alt="${alt}" loading="lazy" />` +
      `<figcaption>${alt}</figcaption></figure>`
    )
  }

  const lines = raw.replace(/\r\n?/g, '\n').split('\n')

  // SVG 插图块:<svg 开行收块,到 </svg> 或明显中文段落收(渲染交给 renderSvgsIn 沙箱)
  const SVG_START = /^\s*<svg\b/i
  const SVG_END = /^\s*<\/svg\s*>\s*$/i
  const svgish = (line: string): boolean =>
    line.includes('<') || line.includes('/>') || line.includes('=')

  // mermaid 源码块:起始关键字行 + 连续的语法行(空行后仍是 mermaid 语法就继续收)
  // 完整渲染交给 renderMermaidIn(懒加载,失败退化成代码块+角标)
  const MERMAID_START =
    /^\s*(graph|flowchart|mindmap|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|xychart-beta|quadrantChart|timeline|gitGraph)\b/i
  const mermaidish = (line: string): boolean =>
    MERMAID_START.test(line) ||
    /^\s*(subgraph\b|end\b|style\b|classDef|click\b|linkStyle|%%|direction\b)/i.test(line) ||
    /-->|---|-\.->|==>|--x|-o\)/.test(line) ||
    /\w\[.*\]|\w\(.*\)|\w\{.*\}/.test(line)

  const looksLikeCode = (line: string): boolean => {
    if (line.trim() === '') return false
    if (/^\s{4,}\S/.test(line)) return true
    if (/^\s*(\/\/|\/\*|\*)/.test(line)) return true
    if (/^\s*[})\]];?\s*$/.test(line)) return true
    if (/[;{]\s*$/.test(line)) return true
    if (/^\s*(function|const|let|var|return|if|for|while|class|import|export|def|async|await)\b/.test(line)) {
      return true
    }
    if (/^\s*[\w.$]+\s*\(.*\)\s*;?\s*$/.test(line) && /[=:]/.test(line)) return true
    return false
  }

  const out: string[] = []
  let buffer: string[] = []
  let bufferIsCode: boolean | null = null

  const flush = (): void => {
    if (buffer.length === 0) return
    while (buffer.length > 0 && (buffer[0] as string).trim() === '') buffer.shift()
    while (buffer.length > 0 && (buffer[buffer.length - 1] as string).trim() === '') buffer.pop()
    if (buffer.length === 0) {
      bufferIsCode = null
      return
    }
    if (bufferIsCode === true) {
      out.push(`<pre class="sec-code"><code>${escaped(buffer.join('\n'))}</code></pre>`)
    } else {
      // 段落内单换行保留为 <br>(父块的换行本身有语义:步骤、条件列举)
      out.push(`<p>${buffer.map(escaped).join('<br>')}</p>`)
    }
    buffer = []
    bufferIsCode = null
  }

  for (let i = 0; i < lines.length; i++) {
    const line = (lines[i] as string)!
    // 图片标记优先:原位替换成真实插图
    const imgHtml = renderImageMarker(line)
    if (imgHtml !== null) {
      flush()
      out.push(imgHtml)
      continue
    }
    // SVG 优先于普通代码启发式:<svg 开块,到 </svg> 收;没闭合就收到明显中文段落为止
    if (SVG_START.test(line)) {
      flush()
      const block: string[] = [line]
      let j = i + 1
      while (
        j < lines.length &&
        !SVG_END.test(lines[j] as string) &&
        (svgish(lines[j] as string) || (lines[j] as string).trim() === '')
      ) {
        block.push(lines[j] as string)
        j++
      }
      if (j < lines.length && SVG_END.test(lines[j] as string)) {
        block.push(lines[j] as string)
        i = j
      } else {
        i = j - 1 // 没找到闭合:收到哪里算哪里,后续行走普通启发式
      }
      out.push(`<pre><code>${escaped(block.join('\n'))}</code></pre>`)
      continue
    }
    // mermaid 优先于普通代码启发式:起始关键字开块,空行后语法行仍连续则继续收
    if (MERMAID_START.test(line)) {
      flush()
      const block: string[] = [line]
      let j = i + 1
      while (j < lines.length) {
        const cur = lines[j] as string
        if (cur.trim() === '') {
          // 空行:向后看第一个非空行,还是 mermaid 语法就吞掉空行继续,否则收块
          let k = j + 1
          while (k < lines.length && (lines[k] as string)!.trim() === '') k++
          if (k < lines.length && mermaidish((lines[k] as string)!)) {
            block.push(cur)
            j++
            continue
          }
          break
        }
        if (!mermaidish(cur) && /[\u4e00-\u9fa5]/.test(cur) && cur.trim().length > 30 && !/[[{(]/.test(cur)) {
          break // 明显是普通中文段落,收块
        }
        block.push(cur)
        j++
      }
      i = j - 1
      out.push(`<pre><code>${escaped(block.join('\n'))}</code></pre>`)
      continue
    }
    const isCode = looksLikeCode(line)
    if (bufferIsCode === null) {
      bufferIsCode = isCode
      buffer.push(line)
      continue
    }
    const breaksBlock =
      (bufferIsCode && !isCode && /[\u4e00-\u9fa5]/.test(line) && line.trim().length > 12) ||
      (!bufferIsCode && isCode)
    if (breaksBlock) {
      flush()
      bufferIsCode = isCode
    }
    buffer.push(line)
  }
  flush()
  return out.join('\n')
}

/** 平滑滚动期间锁住高亮(否则中途经过的节会闪一下又跳回来) */
let lockUntil = 0

/** 点目录:让右栏滚到对应块(不换内容),并更新 URL hash */
function scrollToSection(chunkId: number, smooth = true): void {
  const container = contentEl.value
  const target = document.getElementById(`sec-${chunkId}`)
  if (container === null || target === null) return
  activeId.value = chunkId
  syncTocScroll(chunkId)
  lockUntil = Date.now() + 600 // 平滑滚动大致耗时,期间不做位置反查
  // 右栏是独立滚动容器,用相对它的偏移量精确定位(留 8px 呼吸空间)
  const top =
    target.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 8
  container.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' })
  // hash 只用于记忆位置,不触发路由重载
  history.replaceState(null, '', `#sec-${chunkId}`)
}

/**
 * 滚动时反查「当前在看哪一节」。
 *
 * 用滚动位置直接算,不用 IntersectionObserver:后者的回调只带「状态变化的块」,
 * 多块同时可见时会漏判,滚轮快速滑动时高亮容易停在上一个。
 *
 * 规则:取「顶部已经越过判定线(视口上沿往下 120px)」的最后一节;
 * 一节都还没到就取第一节。这样滚到哪节,左边就跟到哪节。
 */
const ACTIVE_OFFSET_PX = 60

function computeActive(): void {
  if (Date.now() < lockUntil) return // 程序化滚动中,别和用户意图打架
  if (toc.value.length === 0) return
  const container = contentEl.value
  if (container === null) return
  // 基准是右栏容器的上沿(不是浏览器视口):右栏自己滚,容器位置不变
  const base = container.getBoundingClientRect().top
  let current = toc.value[0]?.parent.chunk_id ?? null
  for (const entry of toc.value) {
    const el = document.getElementById(`sec-${entry.parent.chunk_id}`)
    if (el === null) continue
    if (el.getBoundingClientRect().top - base <= ACTIVE_OFFSET_PX) {
      current = entry.parent.chunk_id
    } else {
      break // 后面的节都在下方,不用再看
    }
  }
  if (current !== activeId.value) {
    activeId.value = current
    syncTocScroll(current)
  }
}

/** 让目录里高亮的那一项保持在可视区域内(目录超高时自己滚) */
function syncTocScroll(chunkId: number | null): void {
  if (chunkId === null) return
  const list = tocListEl.value
  const box = tocEl.value
  if (list === null || box === null) return
  const active = list.querySelector<HTMLElement>(`[data-sec="${chunkId}"]`)
  if (active === null) return
  const listRect = box.getBoundingClientRect()
  const itemRect = active.getBoundingClientRect()
  const margin = 24
  if (itemRect.top < listRect.top + margin) {
    box.scrollTop -= listRect.top + margin - itemRect.top
  } else if (itemRect.bottom > listRect.bottom - margin) {
    box.scrollTop += itemRect.bottom - (listRect.bottom - margin)
  }
}

/** 滚动监听:rAF 节流,一帧只算一次 */
let rafPending = false

function onScroll(): void {
  if (rafPending) return
  rafPending = true
  requestAnimationFrame(() => {
    rafPending = false
    computeActive()
  })
}

/** 绑定 / 解绑(右栏容器滚动 + 尺寸变化) */
function bindScrollTracking(): void {
  contentEl.value?.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll)
}

function unbindScrollTracking(): void {
  contentEl.value?.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
}

/** 首次进入:若带 hash 就滚过去,否则停在顶部 */
function restoreScroll(): void {
  const hash = window.location.hash
  if (hash.startsWith('#sec-')) {
    const id = Number(hash.slice(5))
    if (Number.isFinite(id) && document.getElementById(hash) !== null) {
      activeId.value = id
      void nextTick(() => scrollToSection(id, false))
      return
    }
  }
  const first = toc.value.find((e) => e.parent.text.trim().length > 0)
  activeId.value = first ? first.parent.chunk_id : null
  contentEl.value?.scrollTo({ top: 0 })
}

async function load(): Promise<void> {
  const noteId = Number(props.id)
  if (!Number.isFinite(noteId)) {
    note.value = null
    errorText.value = `笔记编号「${props.id}」不合法`
    return
  }
  loading.value = true
  errorText.value = ''
  savedHint.value = ''
  unbindScrollTracking() // 换笔记时先摘掉旧监听,避免叠加
  try {
    note.value = await getNote(noteId)
  } catch (error) {
    note.value = null
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
  if (note.value === null) return
  // 等 v-if 把正文渲染出来再取容器:contentEl/tocEl 到这一步才存在
  await nextTick()
  // mermaid 代码块渲染成图(懒加载;失败退化为代码块+角标,绝不抛错)
  // SVG 插图同理:沙箱 iframe 展示,失败退化代码块
  if (contentEl.value !== null) {
    void renderMermaidIn(contentEl.value)
    void renderSvgsIn(contentEl.value)
  }
  restoreScroll()
  computeActive()
  bindScrollTracking()
}

watch(
  () => props.id,
  () => void load(),
  { immediate: true },
)

onBeforeUnmount(() => {
  unbindScrollTracking()
})

function qualityLabel(quality: string | null): string {
  if (quality === null || quality === '') return '未评级'
  const labels: Record<string, string> = {
    high: '高',
    core: '核心',
    medium: '中',
    low: '低',
    reference: '参考',
    normal: '一般',
  }
  return labels[quality] ?? quality
}

/** ---- 编辑模式 ---- */

function startEdit(): void {
  if (note.value === null) return
  editSnapshot.value = { title: note.value.title, markdown: note.value.markdown }
  editTitle.value = note.value.title
  editMarkdown.value = note.value.markdown
  saveError.value = ''
  savedHint.value = ''
  editing.value = true
}

function cancelEdit(): void {
  editing.value = false
  saveError.value = ''
}

async function saveEdit(): Promise<void> {
  if (note.value === null || saving.value) return
  const snap = editSnapshot.value
  const body: { title?: string; markdown?: string } = {}
  if (editTitle.value !== snap.title) body.title = editTitle.value
  if (editMarkdown.value !== snap.markdown) body.markdown = editMarkdown.value
  if (body.title === undefined && body.markdown === undefined) {
    savedHint.value = '没有改动。'
    editing.value = false
    return
  }
  saving.value = true
  saveError.value = ''
  try {
    const result = await updateNote(note.value.id, body)
    editing.value = false
    await load() // 目录/正文按新 doc 重新渲染(load 会清 savedHint,提示要在其后赋值)
    savedHint.value = result.content_changed
      ? `已保存,检索索引已重建(父块 ${result.chunks_parents} / 子块 ${result.chunks_children})`
      : '已保存。'
  } catch (error) {
    saveError.value = describeApiError(error)
  } finally {
    saving.value = false
  }
}

/** ---- 删除(真删除:正文/切块/向量/原文件一起清,所以必须二次确认) ---- */
const deleting = ref(false)

async function removeNote(): Promise<void> {
  if (note.value === null || deleting.value) return
  const ok = window.confirm(
    `确定删除《${note.value.title}》吗?正文、检索索引和原文件会一起删除,不可恢复。`,
  )
  if (!ok) return
  deleting.value = true
  try {
    await deleteNote(note.value.id)
    await router.push({ name: 'notes' })
  } catch (error) {
    errorText.value = describeApiError(error)
    deleting.value = false
  }
}
</script>

<template>
  <section class="page note-page">
    <RouterLink class="back" :to="{ name: 'notes' }">← 回笔记库</RouterLink>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <template v-else-if="note !== null">
      <header class="page-head note-head">
        <div class="note-head-row">
          <h1 class="page-title">{{ note.title }}</h1>
          <div class="note-actions">
            <button class="btn" type="button" :disabled="editing" @click="startEdit">编辑</button>
            <button
              class="btn btn--danger"
              type="button"
              :disabled="editing || deleting"
              @click="removeNote"
            >
              {{ deleting ? '删除中…' : '删除' }}
            </button>
          </div>
        </div>
        <p v-if="savedHint !== ''" class="note note--ok saved-hint">{{ savedHint }}</p>
        <div class="detail-meta">
          <span class="badge badge--neutral">质量:{{ qualityLabel(note.quality) }}</span>
          <span v-if="note.lang !== ''" class="muted">语言:{{ note.lang }}</span>
          <span v-if="note.tags.length > 0" class="muted">标签:{{ note.tags.join(' · ') }}</span>
          <span v-if="note.source_id !== null" class="muted">来源 #{{ note.source_id }}</span>
          <span class="muted">{{ toc.length }} 节</span>
        </div>
      </header>

      <!-- 编辑模式:标题 + markdown 源码;保存后后端重建切块与向量 -->
      <div v-if="editing" class="editor">
        <label class="editor-field">
          <span class="editor-label">标题</span>
          <input v-model="editTitle" class="input" type="text" maxlength="200" />
        </label>
        <label class="editor-field">
          <span class="editor-label">正文(markdown 源码)</span>
          <textarea
            v-model="editMarkdown"
            class="editor-md"
            spellcheck="false"
            placeholder="# 标题&#10;&#10;正文…"
          ></textarea>
        </label>
        <p class="muted editor-hint">
          保存后按新内容重建检索索引(切块 + 向量化),同内容重复保存不会重嵌。
          图片以 <code>![说明](baleen-image:N)</code> 标记出现:挪动标记行图片就跟着挪,删掉标记行图片也会删除。
        </p>
        <p v-if="saveError !== ''" class="note note--error">{{ saveError }}</p>
        <div class="editor-actions">
          <button class="btn btn--primary" type="button" :disabled="saving" @click="saveEdit">
            {{ saving ? '保存中…' : '保存' }}
          </button>
          <button class="btn btn--ghost" type="button" :disabled="saving" @click="cancelEdit">
            取消
          </button>
        </div>
      </div>

      <div v-else class="reader">
        <!-- 左栏:锚点目录(sticky,独立滚动) -->
        <aside ref="tocEl" class="toc" aria-label="章节目录">
          <div class="toc-inner">
            <p class="toc-title">目录</p>
            <ul v-if="toc.length > 0" ref="tocListEl" class="toc-list">
              <li v-for="entry in toc" :key="entry.parent.chunk_id">
                <button
                  class="toc-item"
                  :class="{ 'toc-item--active': entry.parent.chunk_id === activeId }"
                  :style="{ paddingLeft: `${10 + entry.depth * 14}px` }"
                  type="button"
                  :data-sec="entry.parent.chunk_id"
                  :title="entry.parent.heading_path"
                  @click="scrollToSection(entry.parent.chunk_id)"
                >
                  {{ entry.label }}
                </button>
              </li>
            </ul>
            <p v-else class="muted toc-empty">这篇没有分节记录。</p>
          </div>
        </aside>

        <!-- 右栏:整篇连续正文,每节一个锚点 -->
        <article ref="contentEl" class="content">
          <section
            v-for="entry in toc"
            :id="`sec-${entry.parent.chunk_id}`"
            :key="entry.parent.chunk_id"
            class="sec-block"
          >
            <h2 v-if="entry.label !== '正文'" class="sec-heading" :data-depth="entry.depth">
              {{ entry.label }}
            </h2>
            <!-- 父块原文经 renderSectionText 分块(段落/代码)并逐行转义,不是原文直插 -->
            <div class="sec" v-html="htmlById.get(entry.parent.chunk_id)" />
          </section>

          <p v-if="toc.length === 0" class="empty">这条笔记没有可显示的内容。</p>

          <p class="sec-end muted">— 全文完 —</p>
        </article>
      </div>
    </template>
  </section>
</template>

<style scoped>
.note-page {
  display: flex;
  /* 撑满可视区域,页面本身不滚(滚动交给下面两栏) */
  height: calc(100vh - 96px);
  min-height: 420px;
  flex-direction: column;
}

.back {
  display: inline-block;
  margin-bottom: 12px;
  font-size: 13.5px;
}

.note-head {
  margin-bottom: 18px;
}

.note-head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.note-actions {
  display: flex;
  gap: 8px;
}

.saved-hint {
  margin: 8px 0 0;
}

/* ---------- 编辑模式 ---------- */

.editor {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.editor-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.editor-label {
  color: var(--ink-3);
  font-size: 12.5px;
  font-weight: 600;
}

.editor-md {
  flex: 1;
  min-height: 260px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--ink);
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.7;
  resize: none;
}

.editor-md:focus {
  outline: none;
  border-color: var(--accent);
}

.editor-hint {
  margin: 0;
  font-size: 12.5px;
}

.editor-actions {
  display: flex;
  gap: 10px;
}

.detail-meta {
  display: flex;
  margin-top: 8px;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

/* ---------- 两栏 ---------- */

.reader {
  display: grid;
  /* 关键:两栏都占满剩余高度,各自内部滚动 → 底边天然对齐且都没有空白 */
  flex: 1;
  min-height: 0;
  grid-template-columns: 236px minmax(0, 1fr);
  gap: 24px;
  align-items: stretch;
}

/* 左栏:固定一屏高,目录多了自己滚 */
.toc {
  min-width: 0;
  min-height: 0;
  padding: 14px 10px;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}

/* 左栏不再需要 sticky:它自己就是滚动容器 */
.toc-inner {
  display: contents;
}

.toc-title {
  margin: 0 0 8px;
  padding: 0 6px;
  color: var(--ink-3);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.toc-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.toc-item {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-left: 2px solid transparent;
  border-radius: 6px;
  background: none;
  color: var(--ink-2);
  cursor: pointer;
  font-size: 13px;
  line-height: 1.45;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: background 0.13s ease, color 0.13s ease;
}

.toc-item:hover {
  background: var(--accent-soft);
  color: var(--accent-ink);
}

.toc-item--active {
  border-left-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-weight: 600;
}

.toc-empty {
  padding: 0 6px;
  font-size: 12.5px;
}

/* ---------- 右栏:连续正文 ---------- */

.content {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  /* 给滚动条留点空间,末行不被贴边 */
  padding-right: 6px;
}

.sec-block {
  /* 锚点跳转时给标题留出呼吸空间 */
  scroll-margin-top: 16px;
  padding-bottom: 26px;
  margin-bottom: 26px;
  border-bottom: 1px solid var(--line);
}

.sec-block:last-of-type {
  border-bottom: none;
}

.sec-heading {
  margin: 0 0 14px;
  color: var(--ink);
  font-size: 18px;
  font-weight: 700;
  line-height: 1.4;
}

/* 二级章节稍小一点 */
.sec-heading[data-depth='1'] {
  padding-left: 10px;
  border-left: 3px solid var(--accent-soft);
  font-size: 16px;
}

.sec-heading[data-depth='2'] {
  padding-left: 10px;
  border-left: 3px solid var(--accent-soft);
  color: var(--ink-2);
  font-size: 15px;
}

.sec {
  color: var(--ink);
  font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
  font-size: 14.5px;
  line-height: 1.85;
  word-break: break-word;
}

.sec :deep(p) {
  margin: 0 0 14px;
}

.sec :deep(p:last-child) {
  margin-bottom: 0;
}

.sec :deep(.sec-code) {
  margin: 0 0 16px;
  padding: 13px 16px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f7f9fc;
  color: #22303f;
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre;
}

/* 笔记插图:md 导入时随笔记存字节,阅读页按原位渲染 */
.sec :deep(.note-img) {
  margin: 0 0 16px;
  text-align: center;
}

.sec :deep(.note-img img) {
  max-width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}

.sec :deep(.note-img figcaption) {
  margin-top: 6px;
  color: var(--ink-3);
  font-size: 12.5px;
}

.sec :deep(.img-missing) {
  color: var(--ink-3);
  font-size: 13px;
}

.sec-end {
  margin-top: 8px;
  text-align: center;
  font-size: 12.5px;
}

/* 窄屏:目录折到上方 */
@media (max-width: 900px) {
  .note-page {
    height: auto;
  }

  .reader {
    grid-template-columns: minmax(0, 1fr);
  }

  .toc {
    max-height: 240px;
  }

  .content {
    overflow: visible;
  }
}
</style>
