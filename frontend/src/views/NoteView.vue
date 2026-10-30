<script setup lang="ts">
/**
 * 笔记详情:左侧目录(章节导航) + 右侧正文,两栏。
 *
 * 数据来源:GET /api/notes/{id} 返回的 parents(父块真源)。每块的 heading_path
 * 是「笔记标题 > 一级 > 二级」这种层级路径,拆开取最后一段当章节名、
 * 段数-1 当缩进层级 —— 左侧就成了一份带层级的目录。
 *
 * 选中哪一节记在 URL 的 ?section=<chunk_id> 上,刷新/分享都能回到同一节;
 * 不传则默认第一节。markdown 有正文时额外给一个「全文」入口。
 *
 * markdown 渲染器是手写的(前端不许加第三方依赖):所有原文先 HTML 转义再拼标记,
 * 文档里写了 <script> 也只会显示成文字。
 */
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import { getNote } from '../api/notes'
import type { NoteDetail, NoteParent } from '../api/notes'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps<{ id: string }>()

const route = useRoute()
const router = useRouter()

const note = ref<NoteDetail | null>(null)
const loading = ref(false)
const errorText = ref('')

/** 目录条目:章节名 + 缩进层级 + 原块 */
interface TocEntry {
  parent: NoteParent
  label: string
  depth: number
}

/**
 * heading_path → 目录条目。
 * 「笔记标题 > 一、闭包 > 官方定义」→ label=「官方定义」、depth=2。
 * 只有笔记标题一层(整篇没分节)时 depth=0,显示成单个「正文」。
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
    // 去掉开头与笔记标题相同的那一段(它只是根);标题可能被截断,所以容忍前缀关系
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

/** 当前选中:URL 的 ?section 优先,否则第一节 */
const selectedId = computed<number | null>(() => {
  const raw = route.query.section
  if (typeof raw === 'string') {
    const parsed = Number(raw)
    if (Number.isFinite(parsed)) return parsed
  }
  const first = toc.value[0]
  return first ? first.parent.chunk_id : null
})

const selected = computed<NoteParent | null>(() => {
  if (selectedId.value === null) return null
  return toc.value.find((e) => e.parent.chunk_id === selectedId.value)?.parent ?? null
})

const selectedIndex = computed(() =>
  toc.value.findIndex((e) => e.parent.chunk_id === selectedId.value),
)

const renderedMarkdown = computed(() =>
  note.value === null ? '' : renderMarkdown(note.value.markdown),
)

/** 是否有 markdown 全文可看(没有就只按章节读父块) */
const hasMarkdown = computed(() => (note.value?.markdown ?? '') !== '')

/** 读 URL 的 ?view=full 决定看全文还是单节 */
const viewMode = computed<'section' | 'full'>(() =>
  route.query.view === 'full' && hasMarkdown.value ? 'full' : 'section',
)

function selectSection(chunkId: number): void {
  void router.push({
    name: 'note',
    params: { id: props.id },
    query: { section: String(chunkId) },
  })
}

function selectFull(): void {
  void router.push({
    name: 'note',
    params: { id: props.id },
    query: { view: 'full' },
  })
}

function goNeighbor(step: -1 | 1): void {
  const next = toc.value[selectedIndex.value + step]
  if (next) selectSection(next.parent.chunk_id)
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
  try {
    note.value = await getNote(noteId)
  } catch (error) {
    note.value = null
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
}

// 路由参数变化(从检索页连点两条笔记)时重新拉
watch(() => props.id, () => void load(), { immediate: true })

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
  <section class="page">
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
        <!-- 左栏:目录 -->
        <aside class="toc" aria-label="章节目录">
          <p class="toc-title">目录</p>
          <ul v-if="toc.length > 0" class="toc-list">
            <li v-for="entry in toc" :key="entry.parent.chunk_id">
              <button
                class="toc-item"
                :class="{ 'toc-item--active': entry.parent.chunk_id === selectedId && viewMode === 'section' }"
                :style="{ paddingLeft: `${10 + entry.depth * 14}px` }"
                type="button"
                :title="entry.parent.heading_path"
                @click="selectSection(entry.parent.chunk_id)"
              >
                {{ entry.label }}
              </button>
            </li>
            <li v-if="hasMarkdown">
              <button
                class="toc-item toc-item--full"
                :class="{ 'toc-item--active': viewMode === 'full' }"
                type="button"
                @click="selectFull"
              >
                全文
              </button>
            </li>
          </ul>
          <p v-else class="muted toc-empty">这篇没有分节记录。</p>
        </aside>

        <!-- 右栏:正文 -->
        <article class="content">
          <!-- 全文视图 -->
          <template v-if="viewMode === 'full'">
            <!-- 渲染器内部已先做 HTML 转义,输出只包含自己拼的标签,不是原文直接插进 DOM -->
            <div class="card article md" v-html="renderedMarkdown" />
          </template>

          <!-- 单节视图 -->
          <template v-else-if="selected !== null">
            <div class="section-head">
              <span class="section-path mono">{{ selected.heading_path }}</span>
              <span class="muted section-tokens">{{ selected.token_count }} tokens</span>
            </div>
            <div class="card section-body">{{ selected.text }}</div>

            <nav class="section-nav" aria-label="章节切换">
              <button
                class="btn"
                type="button"
                :disabled="selectedIndex <= 0"
                @click="goNeighbor(-1)"
              >
                ← 上一节
              </button>
              <span class="muted section-pos">
                {{ selectedIndex + 1 }} / {{ toc.length }}
              </span>
              <button
                class="btn"
                type="button"
                :disabled="selectedIndex < 0 || selectedIndex >= toc.length - 1"
                @click="goNeighbor(1)"
              >
                下一节 →
              </button>
            </nav>
          </template>

          <p v-else class="empty">这条笔记没有可显示的内容。</p>
        </article>
      </div>
    </template>
  </section>
</template>

<style scoped>
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

/* ---------- 两栏布局 ---------- */

.reader {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  gap: 22px;
  align-items: start;
}

/* 左栏目录:随页面滚动固定在视口内 */
.toc {
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 120px);
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

.toc-item--full {
  margin-top: 6px;
  border-top: 1px solid var(--line);
  padding-top: 9px;
  color: var(--ink-3);
  font-style: italic;
}

.toc-empty {
  padding: 0 6px;
  font-size: 12.5px;
}

/* ---------- 右栏正文 ---------- */

.content {
  min-width: 0;
}

.article {
  padding: 20px 24px;
}

.section-head {
  display: flex;
  margin-bottom: 10px;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 12px;
}

.section-path {
  color: var(--accent-ink);
  font-size: 12.5px;
  font-weight: 600;
  word-break: break-word;
}

.section-tokens {
  font-size: 12px;
}

.section-body {
  padding: 20px 24px;
  color: var(--ink);
  font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
  font-size: 14.5px;
  line-height: 1.85;
  white-space: pre-wrap;
  word-break: break-word;
}

.section-nav {
  display: flex;
  margin-top: 18px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-pos {
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}

/* 窄屏:目录折到上方,不再并排 */
@media (max-width: 900px) {
  .reader {
    grid-template-columns: minmax(0, 1fr);
  }

  .toc {
    position: static;
    max-height: 260px;
  }
}
</style>
