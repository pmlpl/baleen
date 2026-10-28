<script setup lang="ts">
/**
 * 笔记详情:GET /api/notes/{id} —— 渲染 markdown,下方折叠列父块。
 *
 * markdown 渲染器为什么手写:前端不许新增第三方依赖(AGENTS.md §3.2),
 * 也没有 marked/markdown-it 可用,所以只实现「标题/段落/列表/引用/代码块/行内粗体斜体代码」
 * 这个够用的最小集。安全前提:所有原始文本先做 HTML 转义再拼标记,
 * 上传文档里就算写了 <script> 也只会显示成文字。
 */
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { getNote } from '../api/notes'
import type { NoteDetail } from '../api/notes'

const props = defineProps<{ id: string }>()

const note = ref<NoteDetail | null>(null)
const loading = ref(false)
const errorText = ref('')

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderInline(raw: string): string {
  return escapeHtml(raw)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
}

function renderMarkdown(markdown: string): string {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []
  let listTag: 'ul' | 'ol' | null = null
  let inCode = false
  let codeLines: string[] = []
  let paragraph: string[] = []

  const flushParagraph = (): void => {
    if (paragraph.length === 0) return
    out.push(`<p>${renderInline(paragraph.join(' '))}</p>`)
    paragraph = []
  }

  const closeList = (): void => {
    if (listTag === null) return
    out.push(`</${listTag}>`)
    listTag = null
  }

  for (const line of lines) {
    if (line.trimStart().startsWith('```')) {
      if (inCode) {
        out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = []
        inCode = false
      } else {
        flushParagraph()
        closeList()
        inCode = true
      }
      continue
    }

    if (inCode) {
      codeLines.push(line)
      continue
    }

    if (line.trim() === '') {
      flushParagraph()
      closeList()
      continue
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading !== null) {
      flushParagraph()
      closeList()
      const level = heading[1].length
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
      continue
    }

    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      flushParagraph()
      closeList()
      out.push('<hr />')
      continue
    }

    const ordered = /^\s*\d+\.\s+(.*)$/.exec(line)
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line)
    const listItem = ordered ?? bullet
    if (listItem !== null) {
      flushParagraph()
      const tag = ordered === null ? 'ul' : 'ol'
      if (listTag !== tag) {
        closeList()
        out.push(`<${tag}>`)
        listTag = tag
      }
      out.push(`<li>${renderInline(listItem[1])}</li>`)
      continue
    }

    const quote = /^>\s?(.*)$/.exec(line)
    if (quote !== null) {
      flushParagraph()
      closeList()
      out.push(`<blockquote>${renderInline(quote[1])}</blockquote>`)
      continue
    }

    paragraph.push(line.trim())
  }

  if (inCode) out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  flushParagraph()
  closeList()
  return out.join('\n')
}

const renderedMarkdown = computed(() => (note.value === null ? '' : renderMarkdown(note.value.markdown)))

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

// 路由参数变化(从检索页连点两条笔记)时重新拉,不用重复写 onMounted
watch(() => props.id, () => void load(), { immediate: true })

function qualityLabel(quality: string | null): string {
  if (quality === null || quality === '') return '未评级'
  const labels: Record<string, string> = { high: '高', core: '核心', medium: '中', low: '低' }
  return labels[quality] ?? quality
}
</script>

<template>
  <section class="page">
    <RouterLink class="back" :to="{ name: 'notes' }">← 回笔记库</RouterLink>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <template v-else-if="note !== null">
      <header class="page-head">
        <h1 class="page-title">{{ note.title }}</h1>
        <div class="detail-meta">
          <span class="badge badge--neutral">质量:{{ qualityLabel(note.quality) }}</span>
          <span class="badge badge--neutral">{{ note.status }}</span>
          <span v-if="note.lang !== ''" class="muted">语言:{{ note.lang }}</span>
          <span v-if="note.tags.length > 0" class="muted">标签:{{ note.tags.join(' · ') }}</span>
          <span v-if="note.source_id !== null" class="muted">来源 #{{ note.source_id }}</span>
        </div>
      </header>

      <!-- 渲染器内部已先做 HTML 转义,输出只包含自己拼的标签,不是原文直接插进 DOM -->
      <article v-if="note.markdown !== ''" class="card article md" v-html="renderedMarkdown" />
      <p v-else class="empty article-empty">
        这条笔记没有 markdown 正文(入库时没生成),下面的父块原文是它的真源。
      </p>

      <section class="parents">
        <h2 class="parents-title">父块({{ note.parents.length }})</h2>
        <p class="muted parents-hint">检索命中就是这些父块;子块只是检索用的中间产物,这里不展示。</p>

        <details v-for="parent in note.parents" :key="parent.chunk_id" class="card parent">
          <summary class="parent-summary">
            <span class="mono parent-path">{{ parent.heading_path === '' ? '(无章节标题)' : parent.heading_path }}</span>
            <span class="muted">{{ parent.token_count }} tokens</span>
          </summary>
          <pre class="parent-text">{{ parent.text }}</pre>
        </details>

        <p v-if="note.parents.length === 0" class="empty">这条笔记没有父块记录。</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.back {
  display: inline-block;
  margin-bottom: 12px;
  font-size: 13.5px;
}

.detail-meta {
  display: flex;
  margin-top: 8px;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.article {
  padding: 20px 24px;
  margin-bottom: 26px;
}

.article-empty {
  margin-bottom: 26px;
}

.parents-title {
  font-size: 17px;
}

.parents-hint {
  margin: 4px 0 12px;
}

.parent {
  padding: 10px 14px;
  margin-bottom: 8px;
}

.parent-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}

.parent-path {
  color: var(--accent-ink);
  font-weight: 600;
}

.parent-text {
  margin: 10px 0 0;
  padding: 10px 12px;
  overflow-x: auto;
  border-radius: 8px;
  background: #f7f9fc;
  color: var(--ink-2);
  font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
