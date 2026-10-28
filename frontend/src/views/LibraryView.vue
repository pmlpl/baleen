<script setup lang="ts">
/**
 * 笔记库列表:GET /api/notes?q=&limit=&offset=
 * 搜索框输入防抖 300ms 才发请求;接口缺失(404)时出人话空态,不白屏。
 */
import { ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { listNotes } from '../api/notes'
import type { NoteListItem } from '../api/notes'

/** 输入停下来 300ms 才查(任务书要求的防抖值) */
const DEBOUNCE_MS = 300
const PAGE_LIMIT = 50

const keyword = ref('')
const appliedKeyword = ref('')
const notes = ref<NoteListItem[]>([])
const total = ref(0)
const loading = ref(false)
const errorText = ref('')

/** 请求序号:防抖 + 快速输入时,丢弃过期响应,避免旧结果覆盖新结果 */
let requestSeq = 0

watch(keyword, (value, _previous, onCleanup) => {
  const timer = setTimeout(() => {
    appliedKeyword.value = value.trim()
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
      limit: PAGE_LIMIT,
    })
    if (seq !== requestSeq) return
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

// immediate:true —— 进页面就先拉一次,不用在 onMounted 里重复调用
watch(appliedKeyword, () => void load(), { immediate: true })

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { hour12: false })
}

function qualityLabel(quality: string | null): string {
  if (quality === null || quality === '') return '未评级'
  const labels: Record<string, string> = { high: '高', core: '核心', medium: '中', low: '低' }
  return labels[quality] ?? quality
}

function qualityTone(quality: string | null): string {
  if (quality === 'high' || quality === 'core') return 'ok'
  if (quality === 'medium') return 'warn'
  if (quality === 'low') return 'error'
  return 'neutral'
}
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">笔记库</h1>
      <p class="page-desc">按标题模糊搜(不走向量)。点一条进详情看全文和父块。</p>
    </header>

    <div class="search-bar">
      <input
        v-model="keyword"
        class="input"
        type="search"
        placeholder="按标题搜索…"
        aria-label="按标题搜索笔记"
      />
      <span class="muted count">{{ loading ? '查询中…' : `共 ${total} 条` }}</span>
    </div>

    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <ul v-if="notes.length > 0" class="note-list">
      <li v-for="note in notes" :key="note.id" class="card note-card">
        <RouterLink class="note-link" :to="`/notes/${note.id}`">
          <span class="note-title">{{ note.title }}</span>
          <span class="badge" :class="`badge--${qualityTone(note.quality)}`">
            质量:{{ qualityLabel(note.quality) }}
          </span>
        </RouterLink>
        <div class="note-meta">
          <span>父块 {{ note.chunk_count }}</span>
          <span v-if="note.source_id !== null">来源 #{{ note.source_id }}</span>
          <span v-else>无来源</span>
          <span v-if="note.tags.length > 0" class="tags">{{ note.tags.join(' · ') }}</span>
          <span class="muted">{{ formatTime(note.created_at) }}</span>
        </div>
      </li>
    </ul>

    <p v-else-if="!loading && errorText === ''" class="empty">
      {{ appliedKeyword === ''
        ? '库里还没有笔记。先去「导入」页传一个文件,处理完就会出现在这里。'
        : `没有标题匹配「${appliedKeyword}」的笔记。` }}
    </p>
  </section>
</template>

<style scoped>
.search-bar {
  display: flex;
  margin-bottom: 16px;
  align-items: center;
  gap: 12px;
}

.count {
  white-space: nowrap;
}

.note-list {
  display: flex;
  list-style: none;
  flex-direction: column;
  gap: 10px;
}

.note-card {
  padding: 13px 16px;
  transition: border-color 0.15s ease, transform 0.15s ease;
}

.note-card:hover {
  border-color: #c9d8e6;
  transform: translateY(-1px);
}

.note-link {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  color: var(--ink);
}

.note-link:hover {
  text-decoration: none;
  color: var(--accent-ink);
}

.note-title {
  font-size: 15.5px;
  font-weight: 650;
}

.note-meta {
  display: flex;
  margin-top: 6px;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  color: var(--ink-2);
  font-size: 13px;
}

.tags {
  color: var(--accent-ink);
}
</style>
