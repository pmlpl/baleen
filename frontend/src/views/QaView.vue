<script setup lang="ts">
/**
 * 问答页(阶段2:聊天式多轮)。
 * 左侧会话列表(历史可重开/删除),中部消息流(用户右/助手左),底部输入。
 * assistant 消息渲染 markdown,出处折叠在消息下方;「缓存 xx%」是 DeepSeek
 * 前缀缓存命中率(hit/(hit+miss)),首问没有缓存数据时不显示。
 * 数据全部来自 API(会话/消息已落库),本地 ref 不用 store,不引第三方库。
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getToken } from '../auth/token'
import { describeApiError } from '../api/http'
import { deleteSession, getSession, listSessions, saveSessionNote, streamChat } from '../api/qa'
import type { QaMessage, QaSessionItem } from '../api/qa'
import { renderMarkdown } from '../utils/markdown'

const TOP_K = 5

const router = useRouter()

const sessions = ref<QaSessionItem[]>([])
const currentSessionId = ref<number | null>(null)
/** 本地消息 = 后端消息 + 检索词(当轮响应才有,不落库 → 重开历史会话不回放) */
type ChatMessage = QaMessage & { search_query?: string }
const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
const loadingSession = ref(false)
const savingNote = ref(false)
const savedNote = ref('')
const errorText = ref('')

const msgListEl = ref<HTMLElement | null>(null)

/** 与 messages 一一对应的 assistant 消息 HTML(渲染器内部先 HTML 转义) */
const rendered = computed(() =>
  messages.value.map((m) => (m.role === 'assistant' ? renderMarkdown(m.content) : '')),
)

/** 最新一条助手消息的缓存命中率;null = 当前模型没返回缓存数据 */
const lastCacheRate = computed<number | null>(() => {
  for (let i = messages.value.length - 1; i >= 0; i -= 1) {
    const m = messages.value[i]
    if (m.role === 'assistant') return m.cache_hit_rate
  }
  return null
})

const hasAssistantMessage = computed(() => messages.value.some((m) => m.role === 'assistant'))

async function refreshSessions(): Promise<void> {
  try {
    sessions.value = (await listSessions()).items
  } catch {
    /* 侧栏拉取失败不打断聊天主流程 */
  }
}

onMounted(() => {
  void refreshSessions()
})

function scrollBottom(): void {
  void nextTick(() => {
    const el = msgListEl.value
    if (el !== null) el.scrollTop = el.scrollHeight
  })
}

function startNew(): void {
  currentSessionId.value = null
  messages.value = []
  errorText.value = ''
}

async function openSession(id: number): Promise<void> {
  if (sending.value) return
  loadingSession.value = true
  errorText.value = ''
  try {
    const detail = await getSession(id)
    currentSessionId.value = detail.id
    messages.value = detail.messages
    scrollBottom()
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    loadingSession.value = false
  }
}

/**
 * 导出会话为 Markdown。
 * 不能用 <a download>(浏览器无法给导航请求加 Authorization 头),改成 fetch + blob。
 */
async function exportSession(sessionId: number): Promise<void> {
  try {
    const token = getToken()
    const resp = await fetch(`/api/qa/sessions/${sessionId}/export`, {
      headers: token === null ? {} : { Authorization: `Bearer ${token}` },
    })
    if (!resp.ok) {
      errorText.value = resp.status === 401 ? '登录已过期,请重新登录' : `导出失败(${resp.status})`
      return
    }
    const text = await resp.text()
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `session-${sessionId}.md`
    link.click()
    URL.revokeObjectURL(url)
  } catch {
    errorText.value = '导出失败:连不上后端'
  }
}

async function removeSession(id: number): Promise<void> {
  if (!window.confirm('删除这个会话?消息一并删除,不可恢复。')) return
  try {
    await deleteSession(id)
    if (currentSessionId.value === id) startNew()
    await refreshSessions()
  } catch (error) {
    errorText.value = describeApiError(error)
  }
}

async function send(): Promise<void> {
  if (sending.value) return
  const text = input.value.trim()
  if (text === '') {
    errorText.value = '请输入要问的问题'
    return
  }
  sending.value = true
  errorText.value = ''
  // 乐观显示:你的消息立刻上屏;回答经 SSE 流式增量打进一条助手气泡(打字机效果)
  const tempId = -Date.now()
  messages.value.push({
    id: tempId,
    role: 'user',
    content: text,
    citations: [],
    model: null,
    cost_tokens: 0,
    cache_hit_rate: null,
  })
  input.value = ''
  scrollBottom()
  let streamingIdx: number | null = null
  let usedSearchQuery = ''
  try {
    await streamChat(
      currentSessionId.value,
      text,
      {
        onMeta: (meta) => {
          usedSearchQuery = meta.search_query
          currentSessionId.value = meta.session_id
          const idx = messages.value.findIndex((m) => m.id === tempId)
          if (idx !== -1) messages.value.splice(idx, 1, meta.user_message)
          messages.value.push({
            id: -1,
            role: 'assistant',
            content: '',
            citations: [],
            model: null,
            cost_tokens: 0,
            cache_hit_rate: null,
            search_query: meta.search_query,
          })
          streamingIdx = messages.value.length - 1
          scrollBottom()
        },
        onDelta: (t) => {
          if (streamingIdx !== null && messages.value[streamingIdx] !== undefined) {
            messages.value[streamingIdx].content += t
            scrollBottom()
          }
        },
        onDone: (d) => {
          if (streamingIdx !== null && messages.value[streamingIdx] !== undefined) {
            messages.value[streamingIdx] = {
              ...d.assistant_message,
              search_query: usedSearchQuery,
            }
          }
          void refreshSessions()
        },
        onError: (detail) => {
          errorText.value = detail
        },
      },
      TOP_K,
    )
  } catch (error) {
    // 流没建立起来(网络/后端未起):撤下乐观消息与空气泡,还原文输入
    const idx = messages.value.findIndex((m) => m.id === tempId)
    if (idx !== -1) messages.value.splice(idx, 1)
    if (streamingIdx !== null && messages.value[streamingIdx]?.content === '') {
      messages.value.splice(streamingIdx, 1)
    }
    input.value = text
    errorText.value = describeApiError(error)
  } finally {
    sending.value = false
  }
}

function openNote(noteId: number): void {
  void router.push(`/notes/${noteId}`)
}

/** 阶段4(D2=B):把当前会话整理成 Markdown 笔记,落 notes_out/ 并提交入库 */
async function saveAsNote(): Promise<void> {
  if (savingNote.value || currentSessionId.value === null) return
  savingNote.value = true
  savedNote.value = ''
  try {
    const r = await saveSessionNote(currentSessionId.value)
    savedNote.value = `已存 ${r.note_file}(整理 ${r.organized_chars} 字,已提交入库)`
  } catch (error) {
    savedNote.value = describeApiError(error)
  } finally {
    savingNote.value = false
  }
}

/** 库内出处跳笔记详情;联网出处开新窗口 */
function openCitation(c: { note_id: number | null; url: string | null }): void {
  if (c.url !== null) {
    window.open(c.url, '_blank', 'noopener')
    return
  }
  if (c.note_id !== null) openNote(c.note_id)
}
</script>

<template>
  <section class="page qa-page">
    <header class="page-head">
      <h1 class="page-title">问答</h1>
      <p class="page-desc">
        和笔记库对话:它用笔记内容答、标出处,多轮追问记得上文。「缓存命中率」是模型前缀缓存的命中占比,显示在输入框下方。
      </p>
    </header>

    <div class="qa-layout">
      <aside class="session-side">
        <button class="btn btn--primary new-btn" type="button" @click="startNew">+ 新对话</button>
        <p v-if="sessions.length === 0" class="muted side-empty">还没有历史会话</p>
        <ul class="session-list">
          <li
            v-for="s in sessions"
            :key="s.id"
            class="session-item"
            :class="{ 'session-item--active': s.id === currentSessionId }"
          >
            <button class="session-open" type="button" :title="s.title" @click="openSession(s.id)">
              {{ s.title }}
            </button>
            <button
              class="session-export"
              type="button"
              title="导出为 Markdown"
              aria-label="导出为 Markdown"
              @click.stop="exportSession(s.id)"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            </button>
            <button
              class="session-del"
              type="button"
              aria-label="删除会话"
              @click.stop="removeSession(s.id)"
            >
              ×
            </button>
          </li>
        </ul>
      </aside>

      <div class="chat-main">
        <div class="chat-toolbar">
          <button
            class="btn btn--ghost"
            type="button"
            :disabled="savingNote || currentSessionId === null || messages.length < 2"
            @click="saveAsNote"
          >
            {{ savingNote ? '整理中…' : '存为笔记' }}
          </button>
          <span v-if="savedNote !== ''" class="muted">{{ savedNote }}</span>
        </div>
        <div ref="msgListEl" class="msg-list">
          <p v-if="messages.length === 0 && !sending" class="empty">
            问一句,比如「闭包是什么」;再追问「那怎么用」——它记得上文。
          </p>

          <div
            v-for="(m, i) in messages"
            :key="m.id"
            class="msg-row"
            :class="m.role === 'user' ? 'msg-row--user' : 'msg-row--assistant'"
          >
            <div class="bubble" :class="m.role === 'user' ? 'bubble--user' : 'bubble--assistant'">
              <p v-if="m.role === 'user'" class="user-text">{{ m.content }}</p>
              <p v-else-if="m.content === ''" class="muted">…</p>
              <!-- 渲染器内部已先 HTML 转义,输出只含自己拼的标签,不是原文直接插进 DOM -->
              <article v-else class="md answer-md" v-html="rendered[i]" />

              <p v-if="m.search_query" class="search-hint muted">
                检索词:{{ m.search_query }}
              </p>

              <details
                v-if="m.role === 'assistant' && m.citations.length > 0"
                class="cite-fold"
                :open="i === messages.length - 1"
              >
                <summary class="cite-summary">出处 {{ m.citations.length }} 条</summary>
                <ul class="cite-list">
                  <li
                    v-for="c in m.citations"
                    :key="c.n"
                    class="cite-item"
                    role="link"
                    tabindex="0"
                    @click="openCitation(c)"
                    @keydown.enter="openCitation(c)"
                  >
                    <span class="cite-n mono">{{ c.url !== null ? `[W${c.n}]` : `[${c.n}]` }}</span>
                    <span class="cite-title">{{ c.note_title }}</span>
                    <span v-if="c.heading_path !== null" class="cite-path">{{ c.heading_path }}</span>
                    <span v-if="c.url !== null" class="cite-path">{{ c.url }}</span>
                  </li>
                </ul>
              </details>
              <p
                v-else-if="m.role === 'assistant' && m.citations.length === 0"
                class="cite-empty muted"
              >
                (本次没有引用笔记片段)
              </p>
            </div>
          </div>

          <div v-if="sending" class="msg-row msg-row--assistant">
            <div class="bubble bubble--assistant bubble--pending">思考中…</div>
          </div>
        </div>

        <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

        <form class="chat-input" @submit.prevent="send">
          <input
            v-model="input"
            class="input"
            type="text"
            placeholder="问点什么…(Enter 发送)"
            aria-label="问题"
            :disabled="sending"
          />
          <button class="btn btn--primary" type="submit" :disabled="sending">发送</button>
        </form>

        <p class="cache-status muted" aria-live="polite">
          缓存命中率:
          <span v-if="lastCacheRate !== null" class="cache-value">
            {{ (lastCacheRate * 100).toFixed(1) }}%
          </span>
          <span v-else-if="hasAssistantMessage">—(当前模型未返回缓存数据)</span>
          <span v-else>—</span>
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.qa-layout {
  display: flex;
  align-items: stretch;
  gap: 16px;
}

.session-side {
  flex-shrink: 0;
  width: 210px;
}

.new-btn {
  width: 100%;
  margin-bottom: 10px;
}

.side-empty {
  font-size: 13px;
}

.session-list {
  display: flex;
  list-style: none;
  flex-direction: column;
  gap: 6px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
}

.session-item--active {
  border-color: var(--accent);
}

.session-open {
  flex: 1;
  overflow: hidden;
  padding: 8px 4px 8px 10px;
  border: none;
  background: none;
  color: var(--ink);
  cursor: pointer;
  font-size: 13.5px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-open:hover {
  color: var(--accent-ink);
}

.session-del {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  padding: 4px 8px;
  border: none;
  background: none;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  opacity: 0.75;
  transition: color 0.15s ease, opacity 0.15s ease;
}

.session-del:hover {
  color: var(--err);
  opacity: 1;
}

.session-export {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  padding: 4px 8px;
  border: none;
  background: none;
  color: var(--ink-3);
  cursor: pointer;
  opacity: 0.75;
  transition: color 0.15s ease, opacity 0.15s ease;
}

.session-export:hover {
  color: var(--accent-ink);
  opacity: 1;
}

.chat-main {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
}

.chat-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.msg-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 56vh;
  padding: 6px 2px;
  overflow-y: auto;
}

.msg-row {
  display: flex;
}

.msg-row--user {
  justify-content: flex-end;
}

.msg-row--assistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 86%;
}

.bubble--user {
  padding: 9px 14px;
  border-radius: 12px 12px 3px 12px;
  background: var(--accent);
  color: #fff;
}

.user-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.bubble--assistant {
  padding: 14px 18px;
  border: 1px solid var(--line);
  border-radius: 12px 12px 12px 3px;
  background: #fff;
}

.bubble--pending {
  color: var(--ink-3);
}

.search-hint {
  margin: 12px 0 0;
  font-size: 12.5px;
}

.cite-fold {
  margin-top: 10px;
  border-top: 1px dashed var(--line);
  padding-top: 8px;
}

.cite-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-2);
  cursor: pointer;
  font-size: 13px;
}

.cite-list {
  display: flex;
  list-style: none;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}

.cite-item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #f7f9fc;
  cursor: pointer;
  font-size: 13px;
}

.cite-item:hover {
  background: #eef4fa;
}

.cite-n {
  color: var(--accent-ink);
  font-weight: 700;
}

.cite-title {
  font-weight: 650;
}

.cite-path {
  color: var(--ink-3);
}

.cite-empty {
  margin: 8px 0 0;
  font-size: 12.5px;
}

.chat-input {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.chat-input .btn {
  flex-shrink: 0;
  white-space: nowrap;
}

.cache-status {
  margin: 8px 2px 0;
  font-size: 12.5px;
}

.cache-value {
  color: var(--accent-ink);
  font-weight: 650;
}
</style>
