<script setup lang="ts">
/**
 * 笔记库:书架式网格(5 列 × 6 行 = 每页 30 本),多了翻页。
 *
 * GET /api/notes?q=&limit=&offset= —— 搜索框输入防抖 300ms 才发请求;
 * 翻页/搜索都通过 reload() 重拉,失败给人话提示不白屏。
 */
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { deleteNote, listNotes } from '../api/notes'
import type { NoteListItem } from '../api/notes'

/** 输入停下来 300ms 才查(任务书要求的防抖值) */
const DEBOUNCE_MS = 300
/** 书架:5 列 × 6 行 */
const GRID_COLS = 5
const GRID_ROWS = 6
const PAGE_SIZE = GRID_COLS * GRID_ROWS

const keyword = ref('')
const appliedKeyword = ref('')
const notes = ref<NoteListItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const errorText = ref('')

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const rangeStart = computed(() => (total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1))
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))

/** 请求序号:防抖 + 快速输入/翻页时,丢弃过期响应,避免旧结果覆盖新结果 */
let requestSeq = 0

watch(keyword, (value, _previous, onCleanup) => {
  const timer = setTimeout(() => {
    const next = value.trim()
    // 搜索词变了就回到第一页,否则会停在越界的页码上
    if (next !== appliedKeyword.value) page.value = 1
    appliedKeyword.value = next
  }, DEBOUNCE_MS)
  onCleanup(() => clearTimeout(timer))
})

async function load(): Promise<void> {
  const seq = ++requestSeq
  loading.value = true
  errorText.value = ''
  try {
    const response = await listNotes({
      q: appliedKeyword.value === '' ? undefined : appliedKeyword.value,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
    if (seq !== requestSeq) return
    const lastPage = Math.max(1, Math.ceil(response.total / PAGE_SIZE))
    // 当前页越界(搜索缩小了结果集)→ 回到最后一页,由 watch 重新拉一次
    if (page.value > lastPage) {
      page.value = lastPage
      return
    }
    notes.value = response.items
    total.value = response.total
  } catch (error) {
    if (seq !== requestSeq) return
    notes.value = []
    total.value = 0
    errorText.value = describeApiError(error)
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

watch([appliedKeyword, page], () => void load(), { immediate: true })

function goPage(next: number): void {
  const clamped = Math.min(Math.max(1, next), pageCount.value)
  if (clamped !== page.value) page.value = clamped
}

/** 页码列表:页数多时只显示当前页附近 + 首末页(用 null 表示省略号) */
const pageItems = computed<(number | null)[]>(() => {
  const last = pageCount.value
  const current = page.value
  if (last <= 7) return Array.from({ length: last }, (_, i) => i + 1)
  const items: (number | null)[] = [1]
  const from = Math.max(2, current - 1)
  const to = Math.min(last - 1, current + 1)
  if (from > 2) items.push(null)
  for (let p = from; p <= to; p++) items.push(p)
  if (to < last - 1) items.push(null)
  items.push(last)
  return items
})

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString('zh-CN')
}

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

/** 质量标准配色(E 线 6 档质量枚举) */
function qualityTone(quality: string | null): string {
  if (quality === 'high' || quality === 'core') return 'ok'
  if (quality === 'medium' || quality === 'normal') return 'warn'
  if (quality === 'low' || quality === 'junk') return 'error'
  return 'neutral'
}

/** 书脊配色:按 note.id 稳定取色,同一本书每次进来颜色一致 */
const SPINES = ['#1a73a7', '#2f855a', '#9a5b00', '#7c5cb0', '#b8474d', '#38618c', '#4c7a34']

function spineColor(id: number): string {
  return SPINES[id % SPINES.length] as string
}

/** ---- 删除(真删除:正文/切块/向量/原文件一起清,所以必须二次确认) ---- */
const deletingId = ref<number | null>(null)

async function removeNote(note: NoteListItem): Promise<void> {
  if (deletingId.value !== null) return
  const label = note.title === '' ? `#${note.id}` : `《${note.title}》`
  const ok = window.confirm(`确定删除${label}吗?正文、检索索引和原文件会一起删除,不可恢复。`)
  if (!ok) return
  deletingId.value = note.id
  errorText.value = ''
  try {
    await deleteNote(note.id)
    await load()
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    deletingId.value = null
  }
}
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">笔记库</h1>
      <p class="page-desc">
        你的书架 —— 共 {{ total }} 本，每页 {{ PAGE_SIZE }} 本。点一本书进详情看全文。
      </p>
    </header>

    <div class="search-bar">
      <input
        v-model="keyword"
        class="input"
        type="search"
        placeholder="按标题搜索…"
        aria-label="按标题搜索笔记"
      />
      <span class="muted count">
        {{ loading ? '查询中…' : `${rangeStart}–${rangeEnd} / ${total}` }}
      </span>
    </div>

    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <!-- 书架:固定 5 列;不足一页时也保持格子尺寸,不拉伸 -->
    <ul v-if="notes.length > 0" class="shelf" :class="{ 'shelf--dim': loading }">
      <li v-for="note in notes" :key="note.id" class="book">
        <RouterLink class="book-link" :to="`/notes/${note.id}`">
          <span class="book-cover" :style="{ '--spine': spineColor(note.id) }">
            <span class="book-spine" />
            <span class="book-title">{{ note.title }}</span>
            <span class="book-foot">
              <span class="book-chunks">{{ note.chunk_count }} 段</span>
              <span v-if="note.tags.length > 0" class="book-tags">
                {{ note.tags.slice(0, 2).join(' · ') }}
              </span>
            </span>
          </span>
        </RouterLink>
        <span class="book-meta">
          <span class="badge" :class="`badge--${qualityTone(note.quality)}`">
            {{ qualityLabel(note.quality) }}
          </span>
          <span class="book-meta-right">
            <span class="muted book-date">{{ formatTime(note.created_at) }}</span>
            <button
              class="book-del"
              type="button"
              :disabled="deletingId !== null"
              @click="removeNote(note)"
            >
              {{ deletingId === note.id ? '删除中…' : '删除' }}
            </button>
          </span>
        </span>
      </li>
    </ul>

    <p v-else-if="!loading && errorText === ''" class="empty">
      {{ appliedKeyword === ''
        ? '书架上还没有书。先去「导入」页传一个文件,处理完就会出现在这里。'
        : `没有标题匹配「${appliedKeyword}」的笔记。` }}
    </p>

    <!-- 翻页 -->
    <nav v-if="pageCount > 1" class="pager" aria-label="分页">
      <button class="pager-btn" type="button" :disabled="page === 1 || loading" @click="goPage(page - 1)">
        上一页
      </button>
      <template v-for="(item, idx) in pageItems" :key="`${item ?? 'gap'}-${idx}`">
        <span v-if="item === null" class="pager-gap">…</span>
        <button
          v-else
          class="pager-btn pager-num"
          :class="{ 'pager-num--active': item === page }"
          type="button"
          :disabled="loading"
          :aria-current="item === page ? 'page' : undefined"
          @click="goPage(item)"
        >
          {{ item }}
        </button>
      </template>
      <button
        class="pager-btn"
        type="button"
        :disabled="page === pageCount || loading"
        @click="goPage(page + 1)"
      >
        下一页
      </button>
    </nav>
  </section>
</template>



<style scoped>
.search-bar {
  display: flex;
  margin-bottom: 18px;
  align-items: center;
  gap: 12px;
}

.count {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* ---------- 书架 ---------- */

.shelf {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 20px 18px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.shelf--dim {
  opacity: 0.55;
  transition: opacity 0.15s ease;
}

.book {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
}

.book-link {
  display: block;
  color: inherit;
}

.book-link:hover {
  text-decoration: none;
}

/* 书的封面:圆角矩形 + 左侧书脊 + 竖排标题区 */
.book-cover {
  position: relative;
  display: flex;
  height: 186px;
  flex-direction: column;
  justify-content: space-between;
  padding: 16px 14px 14px 22px;
  border: 1px solid var(--line);
  border-radius: 4px 10px 10px 4px;
  background: var(--panel);
  box-shadow: 0 1px 2px rgba(17, 34, 51, 0.05);
  overflow: hidden;
  transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
}

/* 书脊:左侧一条彩色窄条 */
.book-spine {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 6px;
  background: var(--spine);
  opacity: 0.85;
}

.book-link:hover .book-cover {
  border-color: #c9d8e6;
  box-shadow: 0 6px 18px rgba(17, 34, 51, 0.1);
  transform: translateY(-3px);
}

.book-title {
  display: -webkit-box;
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
  line-clamp: 4;
  word-break: break-word;
}

.book-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  color: var(--ink-3);
  font-size: 12px;
}

.book-tags {
  overflow: hidden;
  color: var(--accent-ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.book-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 0 2px;
}

.book-date {
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
}

.book-meta-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 删除:平时低调,悬停才透出危险色 */
.book-del {
  padding: 2px 5px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--ink-3);
  font-size: 12px;
  cursor: pointer;
  transition: color 0.13s ease, background 0.13s ease;
}

.book-del:hover:not(:disabled) {
  background: #fbeeef;
  color: #b8474d;
}

.book-del:disabled {
  opacity: 0.5;
  cursor: wait;
}

/* ---------- 翻页 ---------- */

.pager {
  display: flex;
  margin-top: 26px;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.pager-btn {
  min-width: 34px;
  padding: 6px 11px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  color: var(--ink-2);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.pager-btn:hover:not(:disabled):not(.pager-num--active) {
  border-color: var(--accent);
  color: var(--accent-ink);
}

.pager-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.pager-num {
  font-variant-numeric: tabular-nums;
}

.pager-num--active {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
  font-weight: 600;
}

.pager-gap {
  padding: 0 2px;
  color: var(--ink-3);
}

/* 窄屏降为 4/3/2 列,避免书被压扁 */
@media (max-width: 1200px) {
  .shelf {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .shelf {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 620px) {
  .shelf {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
