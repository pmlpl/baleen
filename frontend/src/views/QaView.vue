<script setup lang="ts">
/**
 * 问答页(阶段2:聊天式多轮)。
 * 左侧会话列表(历史可重开/删除),中部消息流(用户右/助手左),底部输入。
 * assistant 消息渲染 markdown,出处折叠在消息下方;「缓存 xx%」是 DeepSeek
 * 前缀缓存命中率(hit/(hit+miss)),首问没有缓存数据时不显示。
 * 数据全部来自 API(会话/消息已落库),本地 ref 不用 store,不引第三方库。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getToken } from '../auth/token'
import { describeApiError } from '../api/http'
import { listNotes } from '../api/notes'
import type { NoteListItem } from '../api/notes'
import {
  deleteSession,
  getSession,
  listSessions,
  planSessionNote,
  saveSessionNote,
  streamChat,
  type ChatAttachment,
  type ChatImageAttachment,
} from '../api/qa'

import type {
  NotePlanResponse,
  QaMessage,
  QaSessionItem,
  SaveNoteRequest,
} from '../api/qa'
import { renderMarkdown } from '../utils/markdown'
import { renderMermaidIn } from '../utils/mermaid'
import { renderSvgsIn } from '../utils/svg'

const TOP_K = 5

const router = useRouter()
const route = useRoute()

const sessions = ref<QaSessionItem[]>([])
const currentSessionId = ref<number | null>(null)
/** 本地消息 = 后端消息 + 检索词(当轮响应才有,不落库 → 重开历史会话不回放) */
type ChatMessage = QaMessage & { search_query?: string }
const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
const loadingSession = ref(false)
/** 交互式存笔记:主题计划(非 null 时输入框区域变成选项卡) */
const notePlan = ref<NotePlanResponse | null>(null)
const planningNote = ref(false)
const executingNote = ref(false)
const noteCustomMode = ref(false)
const noteCustomText = ref('')

// ---------------- 对话附件(参考文件给 AI 看,不入库) ----------------

/** 文本类文件扩展名白名单:这些能前端直接读成文本喂给 AI。
 * PDF/DOCX/PPTX 等二进制文档不在此列 —— 走「笔记库页导入→/检索 引用」路径。
 * 图片单独走视觉通道(见 IMAGE_FILE_RE)。 */
const TEXT_FILE_EXTS = new Set([
  'txt', 'md', 'markdown', 'json', 'csv', 'tsv', 'xml', 'yaml', 'yml',
  'html', 'htm', 'css', 'js', 'mjs', 'cjs', 'ts', 'jsx', 'tsx',
  'py', 'java', 'c', 'cpp', 'cc', 'h', 'hpp', 'rs', 'go', 'rb', 'php',
  'sql', 'sh', 'bash', 'zsh', 'bat', 'ps1', 'ini', 'toml', 'cfg', 'conf',
  'log', 'env', 'gitignore', 'dockerfile', 'makefile',
])
/** 图片扩展名:走视觉通道(读成 data URL,后端 attach_images);与文本附件分开存 */
const IMAGE_FILE_RE = /\.(jpe?g|png|gif|webp|bmp)$/i
/** 单张图片上限:base64 进 prompt 太大烧 token,5MB 够大多数截图 */
const ATTACH_IMAGE_MAX_BYTES = 5 * 1024 * 1024
/** 单次提问最多挂的图片数(与后端 ChatRequest.images max_length 对齐) */
const ATTACH_IMAGE_MAX_COUNT = 4
/** 单文件读取上限:超过不读(防内存炸);后端 text 字段上限 20000 字符会再截一次 */
const ATTACH_MAX_BYTES = 2 * 1024 * 1024
/** 后端 AttachmentInput.text 的 max_length;超过按字符截断 */
const ATTACH_MAX_CHARS = 20000

const attachFileInput = ref<HTMLInputElement | null>(null)
/** 已挂的文本附件 chips;随下一次提问发给后端,发送后清空(与引用笔记同口径) */
const attachments = ref<ChatAttachment[]>([])
/** 已挂的图片附件 chips(走视觉通道);与文本附件同消耗口径 */
const imageAttachments = ref<ChatImageAttachment[]>([])

function fileExt(name: string): string {
  const dot = name.lastIndexOf('.')
  // 无扩展名的常见文本文件名(Dockerfile/Makefile)按整体名匹配
  if (dot < 0) return name.toLowerCase()
  return name.slice(dot + 1).toLowerCase()
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '')
    reader.onerror = () => reject(reader.error ?? new Error('读取失败'))
    reader.readAsText(file)
  })
}

/** 把图片文件用 canvas 缩到长边 ≤1024px、导出 JPEG data URL。
 * 网关对大 payload 返 413,视觉模型也不需要原分辨率;统一 JPEG 体积小、模型友好。 */
function resizeImageToDataUrl(
  file: File,
  maxDim = 1024,
  quality = 0.8,
): Promise<{ dataUrl: string; mime: string }> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const url = URL.createObjectURL(file)
    img.onload = () => {
      URL.revokeObjectURL(url)
      let { width, height } = img
      if (width > maxDim || height > maxDim) {
        if (width >= height) {
          height = Math.round((height * maxDim) / width)
          width = maxDim
        } else {
          width = Math.round((width * maxDim) / height)
          height = maxDim
        }
      }
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      const ctx = canvas.getContext('2d')
      if (ctx === null) {
        reject(new Error('canvas 2D 上下文不可用'))
        return
      }
      ctx.drawImage(img, 0, 0, width, height)
      resolve({ dataUrl: canvas.toDataURL('image/jpeg', quality), mime: 'image/jpeg' })
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('图片解码失败'))
    }
    img.src = url
  })
}

/** 把选中的文件分流:图片 → 视觉通道 data URL;文本类 → 文本附件;其余提示走导入。 */
async function addAttachmentFiles(files: FileList | File[]): Promise<void> {
  const list = Array.from(files)
  for (const file of list) {
    const isImage = IMAGE_FILE_RE.test(file.name) || file.type.startsWith('image/')
    if (isImage) {
      if (file.size > ATTACH_IMAGE_MAX_BYTES) {
        errorText.value = `《${file.name}》超过 5MB,不适合当对话图片附件;请去笔记库页「导入笔记」。`
        continue
      }
      if (imageAttachments.value.length >= ATTACH_IMAGE_MAX_COUNT) {
        errorText.value = `一次最多挂 ${ATTACH_IMAGE_MAX_COUNT} 张图片。`
        continue
      }
      try {
        const { dataUrl, mime: im } = await resizeImageToDataUrl(file)
        if (imageAttachments.value.some((a) => a.name === file.name)) continue
        imageAttachments.value.push({ name: file.name, mime: im, data_url: dataUrl })
        errorText.value = ''
      } catch {
        errorText.value = `《${file.name}》读取失败`
      }
      continue
    }
    const ext = fileExt(file.name)
    if (!TEXT_FILE_EXTS.has(ext)) {
      errorText.value =
        `《${file.name}》不是文本类或图片文件;PDF/DOCX/PPTX 请去笔记库页「导入笔记」后用 /检索 引用。`
      continue
    }
    if (file.size > ATTACH_MAX_BYTES) {
      errorText.value = `《${file.name}》超过 2MB,不适合当对话附件;请去笔记库页「导入笔记」。`
      continue
    }
    try {
      let text = await readFileAsText(file)
      if (text.length > ATTACH_MAX_CHARS) {
        text = text.slice(0, ATTACH_MAX_CHARS) + `\n…(已截断,原文共 ${text.length} 字符)`
      }
      // 同名去重:已挂过就不重复加
      if (attachments.value.some((a) => a.name === file.name)) continue
      attachments.value.push({ name: file.name, text })
      errorText.value = ''
    } catch {
      errorText.value = `《${file.name}》读取失败`
    }
  }
}

function removeAttachment(idx: number): void {
  attachments.value.splice(idx, 1)
}

function removeImageAttachment(idx: number): void {
  imageAttachments.value.splice(idx, 1)
}

function onAttachPicked(event: Event): void {
  const inputEl = event.target as HTMLInputElement
  if (inputEl.files !== null && inputEl.files.length > 0) {
    void addAttachmentFiles(inputEl.files)
  }
  inputEl.value = '' // 允许下次选同一个文件
}

/** 拖入文件 → 当附件(替代旧的「拖入即导入笔记库」)。 */
function onChatDrop(event: DragEvent): void {
  const files = event.dataTransfer?.files
  if (files !== undefined && files.length > 0) {
    event.preventDefault()
    void addAttachmentFiles(files)
  }
}

// ---------------- 斜杠命令 ----------------

interface ChatCommand {
  name: string
  desc: string
  keywords: string[]
  run: () => void
}

const slashOpen = ref(false)
const slashActive = ref(0)
const slashMenuEl = ref<HTMLElement | null>(null)

// 高亮项跟着滚进可视区(命令多时菜单内部会滚动)
watch(slashActive, () => {
  void nextTick(() => {
    slashMenuEl.value
      ?.querySelector('.slash-item--active')
      ?.scrollIntoView({ block: 'nearest' })
  })
})

// ---------------- 输入历史(↑ 召回上一条发送内容,↓ 回到草稿) ----------------

const sentHistory = ref<string[]>([])
const historyIndex = ref(-1)
let draftBeforeRecall = ''

function onHistoryKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowUp') {
    if (sentHistory.value.length === 0) return
    if (historyIndex.value === -1) {
      draftBeforeRecall = input.value // 开始回溯前记住草稿
      historyIndex.value = sentHistory.value.length
    }
    if (historyIndex.value === 0) return // 已到最早一条
    historyIndex.value -= 1
    input.value = sentHistory.value[historyIndex.value] ?? ''
    event.preventDefault()
  } else if (event.key === 'ArrowDown') {
    if (historyIndex.value === -1) return // 不在回溯中,放行给光标
    if (historyIndex.value < sentHistory.value.length - 1) {
      historyIndex.value += 1
      input.value = sentHistory.value[historyIndex.value] ?? ''
    } else {
      historyIndex.value = -1
      input.value = draftBeforeRecall // 回到未发送前的草稿
    }
    event.preventDefault()
  }
}

/** 输入框键盘统一入口:命令菜单 → 笔记选择器 → 历史回溯(前者拦截了就不往后传) */
function onInputKeydown(event: KeyboardEvent): void {
  if (slashOpen.value) {
    onSlashKeydown(event)
    if (event.defaultPrevented) return
  }
  if (notePicker.value) {
    onPickerKeydown(event)
    if (event.defaultPrevented) return
  }
  onHistoryKeydown(event)
}

const CHAT_COMMANDS: ChatCommand[] = [
  {
    name: '/新对话',
    desc: '开一个空白会话',
    keywords: ['新', '对话', '会话', '清空', 'new'],
    run: () => {
      startNew()
    },
  },
  {
    name: '/存为笔记',
    desc: '分析本次对话,给出生成选项',
    keywords: ['笔记', '存', '整理', 'note'],
    run: () => {
      void saveAsNote()
    },
  },
  {
    name: '/导出',
    desc: '导出当前会话为 Markdown',
    keywords: ['导出', '下载', 'markdown', 'export'],
    run: () => {
      if (currentSessionId.value !== null) void exportSession(currentSessionId.value)
    },
  },
  {
    name: '/检索',
    desc: '找一篇笔记引用到提问,AI 直接围绕它回答',
    keywords: ['检索', '引用', '搜索', '找', 'search'],
    run: () => {
      openNotePicker()
    },
  },
]

// ---------------- 引用笔记(告诉 AI「在问什么」;检索仍会跑找关联笔记) ----------------

interface NoteRef {
  id: number
  title: string
}

/** 已引用的笔记 chips;随下一次提问发给后端 */
const referencedNotes = ref<NoteRef[]>([])
/** 笔记选择器开着时,输入框变成筛选框,上方列出匹配笔记 */
const notePicker = ref(false)
const pickerNotes = ref<NoteListItem[]>([])
const pickerLoading = ref(false)
let pickerSeq = 0

function openNotePicker(): void {
  notePicker.value = true
  input.value = ''
  void loadPickerNotes('')
}

function closeNotePicker(): void {
  notePicker.value = false
  pickerNotes.value = []
}

function toggleNoteRef(note: NoteRef): void {
  const idx = referencedNotes.value.findIndex((r) => r.id === note.id)
  if (idx >= 0) referencedNotes.value.splice(idx, 1)
  else referencedNotes.value.push(note)
}

function removeNoteRef(id: number): void {
  referencedNotes.value = referencedNotes.value.filter((r) => r.id !== id)
}

/** 输入文字 → 按标题筛笔记(300ms 防抖;过期响应丢弃) */
const loadPickerNotes = async (query: string): Promise<void> => {
  const seq = ++pickerSeq
  pickerLoading.value = true
  try {
    const resp = await listNotes({ q: query === '' ? undefined : query, limit: 8 })
    if (seq !== pickerSeq) return
    pickerNotes.value = resp.items
  } catch {
    if (seq === pickerSeq) pickerNotes.value = []
  } finally {
    if (seq === pickerSeq) pickerLoading.value = false
  }
}

let pickerDebounce: ReturnType<typeof setTimeout> | undefined

function onPickerInput(): void {
  if (!notePicker.value) return
  if (pickerDebounce !== undefined) clearTimeout(pickerDebounce)
  pickerDebounce = setTimeout(() => {
    void loadPickerNotes(input.value.trim())
  }, 300)
}

/** 选择器开着时:Enter 选第一篇,Esc 关闭;不发送消息 */
function onPickerKeydown(event: KeyboardEvent): void {
  if (!notePicker.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closeNotePicker()
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    const first = pickerNotes.value[0]
    if (first !== undefined) {
      toggleNoteRef({ id: first.id, title: first.title })
      closeNotePicker()
      input.value = ''
    }
  }
}

/** 按命令名或关键词过滤;query 为空时展示全部 */
const slashMatches = computed<ChatCommand[]>(() => {
  const query = input.value.slice(1).trim().toLowerCase()
  if (query === '') return CHAT_COMMANDS
  return CHAT_COMMANDS.filter(
    (c) => c.name.toLowerCase().includes(query) || c.keywords.some((k) => k.toLowerCase().includes(query)),
  )
})

function closeSlash(): void {
  slashOpen.value = false
  slashActive.value = 0
}

function updateSlash(): void {
  slashOpen.value = input.value.startsWith('/')
  slashActive.value = 0
}

function executeSlash(cmd: ChatCommand): void {
  closeSlash()
  input.value = ''
  cmd.run()
}

/** 菜单打开时接管方向键/回车/Tab/Esc;Enter 不再提交表单 */
function onSlashKeydown(event: KeyboardEvent): void {
  if (!slashOpen.value) return
  const matches = slashMatches.value
  if (event.key === 'Escape') {
    closeSlash()
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (matches.length > 0) slashActive.value = (slashActive.value + 1) % matches.length
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (matches.length > 0) slashActive.value = (slashActive.value - 1 + matches.length) % matches.length
    return
  }
  if (event.key === 'Enter' || event.key === 'Tab') {
    event.preventDefault()
    const picked = matches[slashActive.value]
    if (picked !== undefined) executeSlash(picked)
  }
}
const errorText = ref('')

const msgListEl = ref<HTMLElement | null>(null)

/** 思考计时:发问到收到第一个 delta 之间的秒数(让长等待看得见进展) */
const thinkingSec = ref(0)
let thinkingTimer: ReturnType<typeof setInterval> | undefined

function startThinkingTimer(): void {
  stopThinkingTimer()
  thinkingSec.value = 0
  thinkingTimer = setInterval(() => {
    thinkingSec.value += 1
  }, 1000)
}

function stopThinkingTimer(): void {
  if (thinkingTimer !== undefined) {
    clearInterval(thinkingTimer)
    thinkingTimer = undefined
  }
}

/** 已开始输出正文(收到首个 delta)→ 不再显示「思考中」 */
const streamingStarted = ref(false)

/** 本轮正在用的模型(meta 事件带来,回答完成后由 lastModel 接管) */
const pendingModel = ref<string | null>(null)

/** 最近一条助手消息用的模型(后端返回 model 字段);null = 还没有回答 */
const lastModel = computed<string | null>(() => {
  for (let i = messages.value.length - 1; i >= 0; i -= 1) {
    const m = messages.value[i]
    if (m.role === 'assistant' && m.model) return m.model
  }
  return null
})

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

/** 状态行显示哪个模型:作答中显示本轮的,否则显示上一条回答用的 */
const displayModel = computed<string | null>(() => pendingModel.value ?? lastModel.value)

async function refreshSessions(): Promise<void> {
  try {
    sessions.value = (await listSessions()).items
  } catch {
    /* 侧栏拉取失败不打断聊天主流程 */
  }
}

onBeforeUnmount(() => {
  stopThinkingTimer()
})

onMounted(() => {
  void refreshSessions()
  // URL 带了 ?session=N → 直接恢复那个会话
  const sid = Number(route.query.session)
  if (Number.isFinite(sid) && sid > 0) void openSession(sid)
})

function scrollBottom(): void {
  void nextTick(() => {
    const el = msgListEl.value
    if (el !== null) el.scrollTop = el.scrollHeight
  })
}

/** 会话 id 同步到 URL(?session=N):刷新/分享都能回到同一会话 */
function syncSessionUrl(id: number | null): void {
  void router.replace({
    query: { ...route.query, session: id === null ? undefined : String(id) },
  })
}

function startNew(): void {
  currentSessionId.value = null
  messages.value = []
  errorText.value = ''
  referencedNotes.value = []
  attachments.value = []
  imageAttachments.value = []
  cancelNotePlan()
  syncSessionUrl(null)
}

async function openSession(id: number): Promise<void> {
  if (sending.value) return
  loadingSession.value = true
  errorText.value = ''
  try {
    const detail = await getSession(id)
    currentSessionId.value = detail.id
    messages.value = detail.messages
    syncSessionUrl(id)
    scrollBottom()
    // 历史消息里可能有 mermaid 图 / SVG 插图
    await nextTick()
    if (msgListEl.value !== null) {
      void renderMermaidIn(msgListEl.value)
      void renderSvgsIn(msgListEl.value)
    }
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
  if (sending.value || slashOpen.value) return
  const text = input.value.trim()
  if (text === '') {
    errorText.value = '请输入要问的问题'
    return
  }
  // 记进输入历史(相邻去重),↑ 可召回
  if (sentHistory.value[sentHistory.value.length - 1] !== text) {
    sentHistory.value.push(text)
  }
  historyIndex.value = -1
  draftBeforeRecall = ''
  sending.value = true
  errorText.value = ''
  streamingStarted.value = false
  startThinkingTimer()
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
  pendingModel.value = null
  try {
    const refNoteIds = referencedNotes.value.map((r) => r.id)
    const attachmentsPayload = attachments.value.map((a) => ({ name: a.name, text: a.text }))
    const imagesPayload = imageAttachments.value.map((i) => ({
      name: i.name,
      mime: i.mime,
      data_url: i.data_url,
    }))
    await streamChat(
      currentSessionId.value,
      text,
      {
        onMeta: (meta) => {
          usedSearchQuery = meta.search_query
          pendingModel.value = meta.model ?? null
          if (currentSessionId.value !== meta.session_id) {
            currentSessionId.value = meta.session_id
            syncSessionUrl(meta.session_id)
          }
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
          if (!streamingStarted.value) {
            streamingStarted.value = true // 正文开始,不再显示「思考中」
            stopThinkingTimer()
          }
          if (streamingIdx !== null && messages.value[streamingIdx] !== undefined) {
            messages.value[streamingIdx].content += t
            scrollBottom()
          }
        },
        onDone: (d) => {
          stopThinkingTimer()
          if (streamingIdx !== null && messages.value[streamingIdx] !== undefined) {
            messages.value[streamingIdx] = {
              ...d.assistant_message,
              search_query: usedSearchQuery,
            }
          }
          // 流式结束才渲染图表(mermaid/SVG 同一纪律):中途渲染会随增量重画抖动
          // (mermaid 当年实测的教训);两个渲染器都幂等,历史/多轮场景重复调用安全
          void nextTick(() => {
            if (msgListEl.value !== null) {
              void renderMermaidIn(msgListEl.value)
              void renderSvgsIn(msgListEl.value)
            }
          })
          void refreshSessions()
        },
        onError: (detail) => {
          stopThinkingTimer()
          errorText.value = detail
        },
      },
      TOP_K,
      undefined,
      refNoteIds,
      attachmentsPayload,
      imagesPayload,
    )
    referencedNotes.value = [] // 引用随问题消耗掉
    attachments.value = [] // 附件随问题消耗掉
    imageAttachments.value = [] // 图片附件随问题消耗掉
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
    stopThinkingTimer()
    sending.value = false
  }
}

function openNote(noteId: number): void {
  void router.push(`/notes/${noteId}`)
}

/** 阶段4(D2=B):把当前会话整理成 Markdown 笔记,落 notes_out/ 并提交入库 */
async function saveAsNote(): Promise<void> {
  if (planningNote.value || executingNote.value || currentSessionId.value === null) return
  planningNote.value = true
  errorText.value = ''
  try {
    notePlan.value = await planSessionNote(currentSessionId.value)
    noteCustomMode.value = false
    noteCustomText.value = ''
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    planningNote.value = false
  }
}

function cancelNotePlan(): void {
  notePlan.value = null
  noteCustomMode.value = false
  noteCustomText.value = ''
}

/** 按用户选的方案生成;结果以助手气泡反馈,输入框恢复 */
async function executeNotePlan(req: SaveNoteRequest): Promise<void> {
  if (executingNote.value || currentSessionId.value === null) return
  executingNote.value = true
  errorText.value = ''
  try {
    const r = await saveSessionNote(currentSessionId.value, req)
    const names = r.notes.map((n) => `《${n.title}》`).join('、')
    const summary =
      r.notes.length > 1
        ? `已按主题生成 ${r.notes.length} 篇笔记:${names}。全部提交入库,可在笔记库查看。`
        : `已生成笔记 ${r.note_file}(整理 ${r.organized_chars} 字,已提交入库)。`
    messages.value.push({
      id: -Date.now(),
      role: 'assistant',
      content: summary,
      citations: [],
      model: null,
      cost_tokens: 0,
      cache_hit_rate: null,
    })
    cancelNotePlan()
    scrollBottom()
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    executingNote.value = false
  }
}

/** 库内出处跳笔记详情;联网出处开新窗口 */
/**
 * 把 URL 显示成可读形式。
 *
 * 联网来源的地址里中文会被百分号编码(如 %E6%B8%AC%E8%A9%A6),直接显示就是乱码。
 * 这里只用 decodeURI 还原可读字符;长度交给 CSS 截断(两行 + 省略号),
 * 完整地址挂在 title 上,鼠标悬停可见。
 *
 * 用 decodeURI 而非 decodeURIComponent:后者会把 %2C %2F 这类保留字也解掉,
 * 可能破坏 URL 结构。
 */
function prettyUrl(raw: string): string {
  try {
    return decodeURI(raw)
  } catch {
    // 非法百分号序列(如裸 % 的路径)→ 原样显示,不能让美化把链接搞坏
    return raw
  }
}

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

      <div class="chat-main" @dragover.prevent @drop.prevent="onChatDrop">
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
                    <span class="cite-title" :title="c.note_title">{{ c.note_title }}</span>
                    <span
                      v-if="c.heading_path !== null"
                      class="cite-path"
                      :title="c.heading_path"
                    >
                      {{ c.heading_path }}
                    </span>
                    <span v-if="c.url !== null" class="cite-path" :title="prettyUrl(c.url)">
                      {{ prettyUrl(c.url) }}
                    </span>
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

          <div v-if="sending && !streamingStarted" class="msg-row msg-row--assistant">
            <div class="bubble bubble--assistant bubble--pending">
              <span class="thinking">思考中</span>
              <span class="thinking-dots" aria-hidden="true">…</span>
              <span v-if="thinkingSec > 0" class="thinking-sec">{{ thinkingSec }}s</span>
              <span v-if="pendingModel !== null" class="thinking-model">
                · {{ pendingModel }}
              </span>
              <span v-if="thinkingSec >= 15" class="thinking-hint">
                （模型较慢，最长可能需要 2 分钟）
              </span>
            </div>
          </div>
        </div>

        <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

        <!-- 附件文件选择器(隐藏,由 📎 按钮/拖放触发;仅文本类) -->
        <input
          ref="attachFileInput"
          class="visually-hidden"
          type="file"
          multiple
          accept=".txt,.md,.markdown,.json,.csv,.tsv,.xml,.yaml,.yml,.html,.htm,.css,.js,.mjs,.cjs,.ts,.jsx,.tsx,.py,.java,.c,.cpp,.cc,.h,.hpp,.rs,.go,.rb,.php,.sql,.sh,.bash,.zsh,.bat,.ps1,.ini,.toml,.cfg,.conf,.log,.env,.jpg,.jpeg,.png,.gif,.webp,.bmp,text/plain,text/markdown,application/json,text/csv,text/yaml,text/x-yaml,image/png,image/jpeg,image/gif,image/webp,image/bmp"
          aria-label="选择要附加的参考文件或图片"
          @change="onAttachPicked"
        />

        <!-- 交互式存笔记:选项卡取代输入框(最后一个选项是自己输入) -->
        <div v-if="notePlan !== null" class="note-plan card">
          <p class="np-title">
            这篇对话涉及 {{ notePlan.topics.length }} 个主题,想怎么整理?
            <span v-if="notePlan.suggestion === 'split'" class="muted np-hint">
              主题之间比较独立,建议拆开
            </span>
          </p>
          <div class="np-options">
            <button
              v-if="notePlan.topics.length > 1"
              class="np-option np-option--primary"
              type="button"
              :disabled="executingNote"
              @click="executeNotePlan({ mode: 'split' })"
            >
              拆成 {{ notePlan.topics.length }} 篇笔记
            </button>
            <button
              v-for="t in notePlan.topics"
              :key="t.id"
              class="np-option"
              type="button"
              :title="t.summary"
              :disabled="executingNote"
              @click="executeNotePlan({ mode: 'topics', topic_ids: [t.id] })"
            >
              只生成:{{ t.title }}
            </button>
            <button
              class="np-option"
              type="button"
              :disabled="executingNote"
              @click="executeNotePlan({ mode: 'single' })"
            >
              全部合成一篇
            </button>
            <button
              class="np-option np-option--custom"
              type="button"
              :disabled="executingNote"
              @click="noteCustomMode = !noteCustomMode"
            >
              都不满意,自己说
            </button>
          </div>
          <div v-if="noteCustomMode" class="np-custom">
            <textarea
              v-model="noteCustomText"
              class="input np-custom-input"
              rows="2"
              placeholder="说说你想怎么整理…(如:只要 axios 的部分 / 按项目拆成两篇)"
            />
            <div class="np-actions">
              <button class="btn btn--ghost" type="button" @click="noteCustomMode = false">
                返回选项
              </button>
              <button
                class="btn btn--primary"
                type="button"
                :disabled="noteCustomText.trim() === '' || executingNote"
                @click="executeNotePlan({ mode: 'split', instruction: noteCustomText.trim() })"
              >
                {{ executingNote ? '整理中…' : '按这个要求整理' }}
              </button>
            </div>
          </div>
          <button class="np-cancel" type="button" :disabled="executingNote" @click="cancelNotePlan">
            取消
          </button>
        </div>

        <form v-else class="chat-input chat-input--anchor" @submit.prevent="send">
          <!-- / 命令菜单:绝对定位在输入框上方 -->
          <div v-if="slashOpen" ref="slashMenuEl" class="slash-menu card">
            <button
              v-for="(c, i) in slashMatches"
              :key="c.name"
              class="slash-item"
              :class="{ 'slash-item--active': i === slashActive }"
              type="button"
              @click="executeSlash(c)"
              @mouseenter="slashActive = i"
            >
              <span class="slash-name mono">{{ c.name }}</span>
              <span class="slash-desc">{{ c.desc }}</span>
            </button>
            <p v-if="slashMatches.length === 0" class="muted slash-empty">
              没有匹配的命令(输入 / 看全部)
            </p>
          </div>
          <!-- /检索 的笔记选择器:输入文字按标题筛,点击引用 -->
          <div v-if="notePicker" class="slash-menu card">
            <button
              v-for="n in pickerNotes"
              :key="n.id"
              class="slash-item"
              type="button"
              @click="toggleNoteRef({ id: n.id, title: n.title }); closeNotePicker(); input = ''"
            >
              <span class="slash-name">{{ n.title }}</span>
              <span class="slash-desc">{{ n.chunk_count }} 段 · 引用到提问</span>
            </button>
            <p v-if="pickerNotes.length === 0 && !pickerLoading" class="muted slash-empty">
              {{ input.trim() === '' ? '输入标题关键词筛选' : '没有匹配的笔记' }}
            </p>
          </div>
          <!-- 已引用的笔记 chips -->
          <div v-if="referencedNotes.length > 0" class="ref-chips">
            <span
              v-for="r in referencedNotes"
              :key="r.id"
              class="ref-chip"
              :title="`取消引用《${r.title}》`"
            >
              引用:《{{ r.title }}》
              <button class="ref-chip-x" type="button" aria-label="取消引用" @click="removeNoteRef(r.id)">
                ×
              </button>
            </span>
          </div>
          <!-- 已挂的附件 chips(参考文件给 AI 看,不入库;随提问消耗) -->
          <div v-if="attachments.length > 0" class="ref-chips">
            <span
              v-for="(a, idx) in attachments"
              :key="a.name"
              class="ref-chip ref-chip--attach"
              :title="`取消附件《${a.name}》`"
            >
              附件:{{ a.name }}
              <button class="ref-chip-x" type="button" aria-label="取消附件" @click="removeAttachment(idx)">
                ×
              </button>
            </span>
          </div>
          <!-- 已挂的图片附件 chips(走视觉通道,不入库;随提问消耗) -->
          <div v-if="imageAttachments.length > 0" class="ref-chips">
            <span
              v-for="(im, idx) in imageAttachments"
              :key="im.name"
              class="ref-chip ref-chip--attach ref-chip--image"
              :title="`取消图片《${im.name}》`"
            >
              <img class="ref-chip-thumb" :src="im.data_url" alt="" />
              {{ im.name }}
              <button class="ref-chip-x" type="button" aria-label="取消图片" @click="removeImageAttachment(idx)">
                ×
              </button>
            </span>
          </div>
          <input
            v-model="input"
            class="input"
            type="text"
            :placeholder="notePicker ? '输入标题筛选,点击上方笔记引用(Esc 退出)' : '问点什么…(输入 / 唤起命令)'"
            aria-label="问题"
            :disabled="sending"
            @input="updateSlash(); onPickerInput()"
            @keydown="onInputKeydown"
          />
          <button
            class="btn attach-btn"
            type="button"
            :disabled="sending"
            title="附加参考文件/图片给 AI 看,不入库"
            aria-label="附加参考文件或图片"
            @click="attachFileInput?.click()"
          >
            📎
          </button>
          <button class="btn btn--primary" type="submit" :disabled="sending">发送</button>
        </form>

        <p class="cache-status muted" aria-live="polite">
          <template v-if="displayModel !== null">
            <span class="model-tag" title="当前使用的模型">{{ displayModel }}</span>
            <span class="status-sep">·</span>
          </template>
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
  overflow: hidden;
  font-weight: 650;
  /* 标题单行,超出省略;完整标题悬停可见 */
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cite-path {
  display: -webkit-box;
  overflow: hidden;
  color: var(--ink-3);
  /* 最多两行,超出省略;完整地址在 title 里(悬停可见) */
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  overflow-wrap: anywhere;
  word-break: break-all;
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

/* 📎 附件按钮:低调图标键,与发送键区分 */
.attach-btn {
  padding: 0 12px;
  font-size: 17px;
  line-height: 1;
  color: var(--ink-3);
  border-color: var(--line);
  background: var(--panel);
}

.attach-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent-ink);
}

.attach-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cache-status {
  margin: 8px 2px 0;
  font-size: 12.5px;
}

.cache-value {
  color: var(--accent-ink);
  font-weight: 650;
}

/* 思考中:秒数 + 久等提示 */
.thinking-sec {
  margin-left: 6px;
  color: var(--ink-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.thinking-hint {
  display: block;
  margin-top: 6px;
  color: var(--ink-3);
  font-size: 12px;
}

.thinking-model {
  margin-left: 4px;
  color: var(--ink-3);
  font-family: 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
}

/* 状态行里的模型名 */
.model-tag {
  padding: 1px 7px;
  border-radius: 5px;
  background: var(--neutral-soft);
  color: var(--ink-2);
  font-size: 12px;
  font-family: 'Cascadia Code', 'Consolas', monospace;
}

.status-sep {
  margin: 0 6px;
  color: var(--ink-3);
}

/* ---- 交互式存笔记选项卡 ---- */
.note-plan {
  padding: 14px 18px;
}

.np-title {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
}

.np-hint {
  font-weight: 400;
  font-size: 12.5px;
  margin-left: 6px;
}

.np-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.np-option {
  padding: 8px 14px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--panel);
  color: var(--ink-2);
  font-size: 13.5px;
  cursor: pointer;
  transition: all 0.15s ease;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.np-option:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent-ink);
  background: var(--accent-soft);
}

.np-option:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.np-option--primary {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-weight: 600;
}

.np-option--custom {
  border-style: dashed;
  color: var(--ink-3);
}

.np-custom {
  margin-top: 12px;
}

.np-custom-input {
  width: 100%;
  resize: vertical;
  font-family: inherit;
}

.np-actions {
  display: flex;
  margin-top: 8px;
  justify-content: flex-end;
  gap: 8px;
}

.np-cancel {
  margin-top: 10px;
  border: none;
  background: none;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 12.5px;
}

.np-cancel:hover:not(:disabled) {
  color: var(--accent);
}

/* ---- 斜杠命令菜单 ---- */
.chat-input--anchor {
  position: relative;
}

.slash-menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  right: 0;
  z-index: 30;
  max-height: 280px;
  padding: 6px;
  overflow-y: auto;
  box-shadow: var(--shadow);
}

.slash-item {
  display: flex;
  width: 100%;
  align-items: baseline;
  gap: 10px;
  padding: 8px 10px;
  border: none;
  border-radius: 7px;
  background: none;
  cursor: pointer;
  text-align: left;
}

.slash-item--active {
  background: var(--accent-soft);
}

.slash-name {
  color: var(--accent-ink);
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.slash-desc {
  overflow: hidden;
  color: var(--ink-2);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.slash-empty {
  padding: 10px;
  font-size: 12.5px;
}

/* ---- 引用笔记 chips ---- */
.ref-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  width: 100%;
}

.ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 260px;
  padding: 3px 8px;
  border: 1px solid var(--accent);
  border-radius: 7px;
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 附件 chip:用中性色调与「引用笔记」区分(它是临时参考,不是库内笔记) */
.ref-chip--attach {
  border-color: var(--line);
  background: var(--panel);
  color: var(--ink-2);
}

/* 图片附件 chip:带缩略图 */
.ref-chip--image {
  gap: 5px;
  padding: 2px 8px 2px 3px;
}

.ref-chip-thumb {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  object-fit: cover;
  border-radius: 4px;
}

.ref-chip-x {
  padding: 0 2px;
  border: none;
  background: none;
  color: var(--accent-ink);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
}

.ref-chip-x:hover {
  color: var(--err);
}
</style>
