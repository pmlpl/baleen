<script setup lang="ts">
/**
 * 问答页:POST /api/qa —— 检索命中 → LLM 用笔记库内容答 + 出处。
 * 答案里的 [n] 对应下方「出处」列表的第 n 条;点出处跳笔记详情。
 * 无状态:问一次答一次,不记历史(多轮会话是后续路线图)。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import { askQuestion } from '../api/qa'
import type { QaResponse } from '../api/qa'
import { renderMarkdown } from '../utils/markdown'

const TOP_K = 5

const router = useRouter()

const question = ref('')
const loading = ref(false)
const errorText = ref('')
const result = ref<QaResponse | null>(null)
/** 是否问过:区分「还没问」和「问了但没拿到答案」 */
const asked = ref(false)

const renderedAnswer = computed(() =>
  result.value === null ? '' : renderMarkdown(result.value.answer),
)

async function submit(): Promise<void> {
  if (loading.value) return
  const text = question.value.trim()
  if (text === '') {
    errorText.value = '请输入要问的问题'
    return
  }
  loading.value = true
  errorText.value = ''
  try {
    result.value = await askQuestion(text, TOP_K)
    asked.value = true
  } catch (error) {
    result.value = null
    asked.value = true
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
}

function openNote(noteId: number): void {
  void router.push(`/notes/${noteId}`)
}
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">问答</h1>
      <p class="page-desc">
        问一句,它用你笔记库里的内容答,并标出出自哪条笔记哪一节。答案里的 [n] 对应下方出处。
      </p>
    </header>

    <form class="search-bar" @submit.prevent="submit">
      <input
        v-model="question"
        class="input"
        type="search"
        placeholder="例:闭包是什么?怎么取消一个 axios 请求?"
        aria-label="问题"
      />
      <button class="btn btn--primary" type="submit" :disabled="loading">
        {{ loading ? '思考中…' : '提问' }}
      </button>
    </form>

    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <template v-if="result !== null">
      <p class="result-meta muted">
        耗时 {{ result.took_ms }} ms · {{ result.citations.length }} 条出处 · {{ result.model }}
      </p>

      <!-- 渲染器内部已先 HTML 转义,输出只含自己拼的标签,不是原文直接插进 DOM -->
      <article class="card md answer" v-html="renderedAnswer" />

      <section v-if="result.citations.length > 0" class="cites">
        <h2 class="cites-title">出处</h2>
        <ul class="cite-list">
          <li
            v-for="c in result.citations"
            :key="c.n"
            class="card cite"
            role="link"
            tabindex="0"
            @click="openNote(c.note_id)"
            @keydown.enter="openNote(c.note_id)"
          >
            <div class="cite-head">
              <span class="cite-n mono">[{{ c.n }}]</span>
              <span class="cite-title">{{ c.note_title }}</span>
            </div>
            <p v-if="c.heading_path !== null" class="cite-path">{{ c.heading_path }}</p>
            <p class="cite-snippet">{{ c.snippet }}</p>
          </li>
        </ul>
      </section>
    </template>

    <p v-else-if="asked && !loading && errorText === ''" class="empty">
      没拿到答案。换个问法再试,或先去「导入」页补几篇笔记。
    </p>

    <p v-else-if="!asked" class="empty">
      输入一句中文问题,这里会用笔记库里的内容回答,并标出出处。
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

.answer {
  padding: 20px 24px;
  margin-bottom: 22px;
}

.cites-title {
  font-size: 16px;
  margin-bottom: 10px;
}

.cite-list {
  display: flex;
  list-style: none;
  flex-direction: column;
  gap: 10px;
}

.cite {
  padding: 12px 14px;
  cursor: pointer;
  transition: border-color 0.15s ease, transform 0.15s ease;
}

.cite:hover {
  border-color: #c9d8e6;
  transform: translateY(-1px);
}

.cite:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.cite-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.cite-n {
  color: var(--accent-ink);
  font-weight: 700;
}

.cite-title {
  font-size: 15px;
  font-weight: 650;
}

.cite-path {
  margin: 4px 0 6px;
  color: var(--ink-2);
  font-size: 13px;
}

.cite-snippet {
  margin: 0;
  color: var(--ink-2);
  font-size: 13.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
