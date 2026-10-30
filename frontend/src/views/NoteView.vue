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
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { getNote } from '../api/notes'
import type { NoteDetail, NoteParent } from '../api/notes'

const props = defineProps<{ id: string }>()

const note = ref<NoteDetail | null>(null)
const loading = ref(false)
const errorText = ref('')

/** 当前视口内的章节 id,用于目录高亮 */
const activeId = ref<number | null>(null)
/** 目录列表元素(高亮项自动滚进可视区用) */
const tocListEl = ref<HTMLElement | null>(null)

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

/** 每个父块 id → 它的排版后 HTML */
const htmlById = computed<Map<number, string>>(() => {
  const map = new Map<number, string>()
  if (note.value === null) return map
  for (const parent of note.value.parents) {
    map.set(parent.chunk_id, renderSectionText(parent.text))
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
function renderSectionText(raw: string): string {
  const escaped = (s: string): string =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

  const lines = raw.replace(/\r\n?/g, '\n').split('\n')

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

  for (const line of lines) {
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

/** 点目录:滚动到对应块(不换内容),并更新 URL hash */
function scrollToSection(chunkId: number, smooth = true): void {
  const target = document.getElementById(`sec-${chunkId}`)
  if (target === null) return
  activeId.value = chunkId
  syncTocScroll(chunkId)
  lockUntil = Date.now() + 600 // 平滑滚动大致耗时,期间不做位置反查
  target.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' })
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
const ACTIVE_OFFSET_PX = 120

function computeActive(): void {
  if (Date.now() < lockUntil) return // 程序化滚动中,别和用户意图打架
  if (toc.value.length === 0) return
  let current = toc.value[0]?.parent.chunk_id ?? null
  for (const entry of toc.value) {
    const el = document.getElementById(`sec-${entry.parent.chunk_id}`)
    if (el === null) continue
    if (el.getBoundingClientRect().top - ACTIVE_OFFSET_PX <= 0) {
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

/** 让目录里高亮的那一项保持在可视区域内(目录自己会滚动) */
function syncTocScroll(chunkId: number | null): void {
  if (chunkId === null) return
  const list = tocListEl.value
  if (list === null) return
  const active = list.querySelector<HTMLElement>(`[data-sec="${chunkId}"]`)
  if (active === null) return
  const listRect = list.getBoundingClientRect()
  const itemRect = active.getBoundingClientRect()
  const margin = 24
  if (itemRect.top < listRect.top + margin) {
    list.scrollTop -= listRect.top + margin - itemRect.top
  } else if (itemRect.bottom > listRect.bottom - margin) {
    list.scrollTop += itemRect.bottom - (listRect.bottom - margin)
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

/** 绑定 / 解绑(window 滚动 + 尺寸变化) */
function bindScrollTracking(): void {
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll)
}

function unbindScrollTracking(): void {
  window.removeEventListener('scroll', onScroll)
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
  window.scrollTo({ top: 0 })
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
  unbindScrollTracking() // 换笔记时先摘掉旧监听,避免叠加
  try {
    note.value = await getNote(noteId)
    await nextTick()
    restoreScroll()
    computeActive()
    bindScrollTracking()
  } catch (error) {
    note.value = null
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
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
</script>

<template>
  <section class="page note-page">
    <RouterLink class="back" :to="{ name: 'notes' }">← 回笔记库</RouterLink>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <template v-else-if="note !== null">
      <header class="page-head note-head">
        <h1 class="page-title">{{ note.title }}</h1>
        <div class="detail-meta">
          <span class="badge badge--neutral">质量:{{ qualityLabel(note.quality) }}</span>
          <span v-if="note.lang !== ''" class="muted">语言:{{ note.lang }}</span>
          <span v-if="note.tags.length > 0" class="muted">标签:{{ note.tags.join(' · ') }}</span>
          <span v-if="note.source_id !== null" class="muted">来源 #{{ note.source_id }}</span>
          <span class="muted">{{ toc.length }} 节</span>
        </div>
      </header>

      <div class="reader">
        <!-- 左栏:锚点目录(sticky,独立滚动) -->
        <aside class="toc" aria-label="章节目录">
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
        </aside>

        <!-- 右栏:整篇连续正文,每节一个锚点 -->
        <article class="content">
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
  min-height: 0;
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
  grid-template-columns: 236px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

/* 左栏:锚点目录,随页面固定 */
.toc {
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 140px);
  overflow-y: auto;
  padding: 14px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
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

.sec-end {
  margin-top: 8px;
  text-align: center;
  font-size: 12.5px;
}

/* 窄屏:目录折到上方 */
@media (max-width: 900px) {
  .reader {
    grid-template-columns: minmax(0, 1fr);
  }

  .toc {
    position: static;
    max-height: 240px;
  }
}
</style>
