<script setup lang="ts">
/**
 * 检索页:POST /api/search —— 混合检索(向量路 + 词法路),RRF 融合。
 * 命中单位是父块;空 hits 是正常结果(库里没相关内容),不许画成错误。
 */
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import { searchNotes } from '../api/search'
import type { SearchResponse } from '../api/search'

/** 父块先只露 300 字,点开看全 */
const PREVIEW_CHARS = 300
const TOP_K = 5

const router = useRouter()

const keyword = ref('')
const loading = ref(false)
const errorText = ref('')
const result = ref<SearchResponse | null>(null)
/** 是否搜过:用来区分「还没搜」和「搜了但没命中」 */
const searched = ref(false)
/** 已展开的命中(按 parent_id) */
const expanded = reactive(new Set<number>())

const hits = computed(() => result.value?.hits ?? [])

async function submit(): Promise<void> {
  if (loading.value) return
  const text = keyword.value.trim()
  if (text === '') {
    errorText.value = '请输入要检索的内容'
    return
  }
  loading.value = true
  errorText.value = ''
  expanded.clear()
  try {
    result.value = await searchNotes(text, TOP_K)
    searched.value = true
  } catch (error) {
    result.value = null
    searched.value = true
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
}

function toggle(parentId: number): void {
  if (expanded.has(parentId)) {
    expanded.delete(parentId)
  } else {
    expanded.add(parentId)
  }
}

function preview(text: string): string {
  return text.length <= PREVIEW_CHARS ? text : `${text.slice(0, PREVIEW_CHARS)}…`
}

function isLong(text: string): boolean {
  return text.length > PREVIEW_CHARS
}

function formatScore(score: number): string {
  return score.toFixed(4)
}

function openNote(noteId: number): void {
  void router.push(`/notes/${noteId}`)
}
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">检索</h1>
      <p class="page-desc">
        向量 + 词法两路召回,RRF 融合作序。点任意命中跳到笔记详情。
      </p>
    </header>

    <form class="search-bar" @submit.prevent="submit">
      <input
        v-model="keyword"
        class="input"
        type="search"
        placeholder="想查什么?例:内存泄漏怎么排查"
        aria-label="检索关键词"
      />
      <button class="btn btn--primary" type="submit" :disabled="loading">
        {{ loading ? '检索中…' : '检索' }}
      </button>
    </form>

    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <p v-if="result !== null && hits.length > 0" class="result-meta muted">
      命中 {{ hits.length }} 条 · 耗时 {{ result.took_ms }} ms · query「{{ result.query }}」
    </p>

    <ul v-if="hits.length > 0" class="hit-list">
      <li
        v-for="hit in hits"
        :key="hit.parent_id"
        class="card hit"
        role="link"
        tabindex="0"
        @click="openNote(hit.note_id)"
        @keydown.enter="openNote(hit.note_id)"
      >
        <div class="hit-head">
          <span class="hit-title">{{ hit.note_title }}</span>
          <span class="hit-score mono">rrf {{ formatScore(hit.rrf_score) }}</span>
        </div>

        <div class="hit-sub">
          <span v-if="hit.heading_path !== ''" class="hit-path">{{ hit.heading_path }}</span>
          <!-- 为 null 的那一路没召回到这个块,就不显示对应徽章 -->
          <span v-if="hit.vector_rank !== null" class="badge badge--ok">向量 #{{ hit.vector_rank }}</span>
          <span v-if="hit.lexical_rank !== null" class="badge badge--neutral">
            词法 #{{ hit.lexical_rank }}
          </span>
          <span v-if="hit.vector_rank !== null && hit.lexical_rank !== null" class="muted">
            两路都命中,最可信
          </span>
        </div>

        <p class="hit-text">
          {{ expanded.has(hit.parent_id) ? hit.parent_text : preview(hit.parent_text) }}
        </p>

        <button
          v-if="isLong(hit.parent_text)"
          class="btn btn--ghost"
          type="button"
          @click.stop="toggle(hit.parent_id)"
        >
          {{ expanded.has(hit.parent_id) ? '收起' : '展开全文' }}
        </button>
      </li>
    </ul>

    <!-- 空命中是正常结果:中性文案,不是报错 -->
    <p v-else-if="searched && !loading && errorText === ''" class="empty">
      库里没有相关内容。换个说法再试,或先去「导入」页补几篇笔记。
    </p>

    <p v-else-if="!searched" class="empty">
      输入一句中文问题,这里会返回带出处的命中(父块 + 章节路径 + 两路名次)。
    </p>
  </section>
</template>

<style scoped>
.search-bar {
  display: flex;
  margin-bottom: 16px;
  align-items: center;
  gap: 10px;
}

.result-meta {
  margin-bottom: 12px;
}

.hit-list {
  display: flex;
  list-style: none;
  flex-direction: column;
  gap: 12px;
}

.hit {
  padding: 14px 16px;
  cursor: pointer;
  transition: border-color 0.15s ease, transform 0.15s ease;
}

.hit:hover {
  border-color: #c9d8e6;
  transform: translateY(-1px);
}

.hit:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.hit-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
}

.hit-title {
  font-size: 15.5px;
  font-weight: 650;
}

.hit-score {
  margin-left: auto;
  color: var(--accent-ink);
}

.hit-sub {
  display: flex;
  margin-top: 6px;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.hit-path {
  color: var(--ink-2);
}

.hit-text {
  margin: 10px 0 8px;
  color: var(--ink-2);
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
