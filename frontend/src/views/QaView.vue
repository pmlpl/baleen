<script setup lang="ts">
/**
 * 问答页(聊天式多轮)·单列对话流:出血形态——整页就是一张摊满视口的宣纸(App.vue
 * stage--bleed 撤掉桌面留白并钳住视口高度),左书签绳(会话)、中间通栏对话列
 * (两侧只留 24px 呼吸边距;AI 回答文章式排版无气泡;用户提问右对齐轻量块,
 * 衬线+右缘朱砂细线)、底浅纸墨槽(输入,不再是大面积黑)。
 * 纸面高度钳在视口内,消息区 flex 撑满并内部滚动,输入栏永久钉在视口底部;
 * 思考状态复用输入区上方的状态行,不再插临时占位行进正文流。
 * assistant 消息渲染 markdown,出处只折叠在文末;点正文 [n]/[W n] 徽章 = 直接跳该出处
 * (库内笔记本页打开靠 keep-alive 保住对话,联网出处开新窗)。
 * 「缓存 xx%」是前缀缓存的会话平均命中率(Σhit/Σ(hit+miss)),首问没有缓存数据时不显示。
 * 数据全部来自 API(会话/消息已落库),本地 ref 不用 store,不引第三方库。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ComponentPublicInstance, Directive } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getToken } from '../auth/token'
import { ApiError, describeApiError } from '../api/http'
import { listNotes } from '../api/notes'
import type { NoteListItem } from '../api/notes'
import { parseAttachment } from '../api/parse'
import { confirmDialog } from '../utils/confirm'
import {
  SESSION_TITLE_MAX,
  deleteSession,
  getSession,
  listSessions,
  planSessionNote,
  renameSession,
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
import { renderMindmapsIn } from '../utils/mindmap'
import { renderSvgsIn } from '../utils/svg'

import NotePicker from '../components/NotePicker.vue'
import SlashMenu from '../components/SlashMenu.vue'

const TOP_K = 5

const router = useRouter()
const route = useRoute()

const sessions = ref<QaSessionItem[]>([])
const currentSessionId = ref<number | null>(null)

// v37 起纸篓是后端软删除(status='archived'),而**所有检索查询本来就过滤
// status='active'** —— 纸篓里的笔记不可能被命中,所以原先那个「篓」标已无意义,删除。

/** 本地展示:这条消息发出时带走的引用/附件/图片(仅当轮显示,不落库 → 重开会话不回放) */
interface SentExtras {
  notes: { id: number; title: string }[]
  files: string[]
  images: { name: string; data_url: string }[]
}

/** 本地消息 = 后端消息 + 检索词(当轮响应才有,不落库 → 重开历史会话不回放) */
type ChatMessage = QaMessage & { search_query?: string; sent_extras?: SentExtras }
const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
/** 引用/附件 chips 嵌进输入框后,真输入框藏在壳里;点壳的空白处要把它聚焦回来 */
const qaInputEl = ref<HTMLInputElement | null>(null)

function focusQaInput(): void {
  qaInputEl.value?.focus()
}

/**
 * 输入框里的「/」钮:等同于用户手打一个斜杠 —— 写入同一个 input,
 * 再走 updateSlash 把菜单打开,后续键盘路由与手打完全一致。
 * 草稿不用管:input 的 watch 已有「/ 开头一律不落盘」的值判据。
 */
function openSlashFromButton(): void {
  input.value = '/'
  updateSlash()
  focusQaInput()
}
const loadingSession = ref(false)
/** 交互式存笔记:主题计划(非 null 时输入框区域变成选项卡) */
const notePlan = ref<NotePlanResponse | null>(null)
const planningNote = ref(false)
const executingNote = ref(false)
const noteCustomMode = ref(false)
const noteCustomText = ref('')

// ---------------- 对话附件(参考文件给 AI 看,不入库) ----------------

/** 图片扩展名:走视觉通道(读成 data URL,后端 attach_images);与文档附件分开存 */
const IMAGE_FILE_RE = /\.(jpe?g|png|gif|webp|bmp)$/i
/** 单张图片上限:base64 进 prompt 太大烧 token,5MB 够大多数截图 */
const ATTACH_IMAGE_MAX_BYTES = 5 * 1024 * 1024
/** 单次提问最多挂的图片数(与后端 ChatRequest.images max_length 对齐) */
const ATTACH_IMAGE_MAX_COUNT = 4
/** 单次提问最多挂的文档附件数(与后端 ChatRequest.attachments max_length=5 对齐) */
const ATTACH_TEXT_MAX_COUNT = 5

const attachFileInput = ref<HTMLInputElement | null>(null)
/** 已挂的文本附件 chips;随下一次提问发给后端,发送后清空(与引用笔记同口径) */
const attachments = ref<ChatAttachment[]>([])
/** 已挂的图片附件 chips(走视觉通道);与文本附件同消耗口径 */
const imageAttachments = ref<ChatImageAttachment[]>([])

/** data URL → Blob(后端 /api/parse 回传的图片都是 data URL 形态,缩图函数吃 Blob)。 */
async function dataUrlToBlob(dataUrl: string): Promise<Blob> {
  const resp = await fetch(dataUrl)
  return await resp.blob()
}

/** 把图片(Blob/File 均可)用 canvas 缩到长边 ≤1024px、导出 JPEG data URL。
 * 网关对大 payload 返 413,视觉模型也不需要原分辨率;统一 JPEG 体积小、模型友好。 */
function resizeImageToDataUrl(
  file: Blob,
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

/** 把选中的文件分流:图片 → 前端视觉通道;文档类一律交后端 /api/parse 统一解析(G0,不入库)。
 * 格式白名单/编码探测/HTML 剥 base64/大文件分析分层都在后端,前端不再读文本。 */
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
    if (attachments.value.length >= ATTACH_TEXT_MAX_COUNT) {
      errorText.value = `一次最多挂 ${ATTACH_TEXT_MAX_COUNT} 个文档附件(与后端上限对齐)。`
      continue
    }
    // 解析可能要几十秒(扫描件逐页视觉识别 / 大文件 map-reduce 分析),给明确的进行中提示
    errorText.value = `正在解析《${file.name}》…`
    try {
      const resp = await parseAttachment(file)
      if (resp.status !== 'ok') {
        errorText.value = `《${file.name}》:${resp.message ?? '解析失败'}`
        continue
      }
      // 正文进文档附件(全文或分析报告,分层在后端做完);同名去重
      if (resp.text !== '' && !attachments.value.some((a) => a.name === file.name)) {
        attachments.value.push({
          name: file.name,
          text: resp.text,
          analyzed: resp.mode === 'analyzed',
        })
      }
      // 文内图片(md/html 的 data URI / 本地图)转前端视觉通道,与直接挂图片同一条路(canvas 缩图)
      for (const img of resp.images) {
        if (imageAttachments.value.length >= ATTACH_IMAGE_MAX_COUNT) {
          errorText.value = `一次最多挂 ${ATTACH_IMAGE_MAX_COUNT} 张图片,多余的已丢弃。`
          break
        }
        if (imageAttachments.value.some((a) => a.name === img.name)) continue
        const blob = await dataUrlToBlob(img.data_url)
        const { dataUrl, mime } = await resizeImageToDataUrl(blob)
        imageAttachments.value.push({ name: img.name, mime, data_url: dataUrl })
      }
      // 后端 warnings(编码容错/图片超限/大文件已分析等)透传给用户
      errorText.value = resp.warnings.length > 0 ? `《${file.name}》:${resp.warnings.join(';')}` : ''
    } catch (error) {
      errorText.value = `《${file.name}》解析失败:${describeApiError(error)}`
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

// ---------------- 斜杠命令(菜单 UI 在 components/SlashMenu.vue;输入框是共享的,开关/过滤/键盘路由留在本页) ----------------

interface ChatCommand {
  name: string
  desc: string
  keywords: string[]
  run: () => void
}

const slashOpen = ref(false)
const slashActive = ref(0)

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

/** 输入框键盘统一入口:笔记选择器 → 命令菜单 → 空输入 Backspace 删 chip → 历史回溯(前者拦截了就不往后传) */
function onInputKeydown(event: KeyboardEvent): void {
  // picker 优先:开着时 Esc 关的是 picker,不会先被 slash 菜单吃掉
  if (notePicker.value) {
    onPickerKeydown(event)
    if (event.defaultPrevented) return
  }
  if (slashOpen.value) {
    onSlashKeydown(event)
    if (event.defaultPrevented) return
  }
  // 输入框为空时 Backspace 从后往前删 chip:图片 → 文本附件 → 引用笔记
  if (event.key === 'Backspace' && input.value === '' && !notePicker.value && !slashOpen.value) {
    if (imageAttachments.value.length > 0) {
      imageAttachments.value.pop()
      event.preventDefault()
      return
    }
    if (attachments.value.length > 0) {
      attachments.value.pop()
      event.preventDefault()
      return
    }
    if (referencedNotes.value.length > 0) {
      referencedNotes.value.pop()
      event.preventDefault()
      return
    }
  }
  onHistoryKeydown(event)
}

/**
 * 斜杠命令(**只留唯一入口的**,主人 2026-11-25 铁律「不重叠功能和内容」)。
 *
 * 已删两条 —— 它们各自已有别的入口,留着就是同一功能的第二个入口:
 * - `/新对话` → 侧栏「+ 新对话」按钮(显式、一眼懂)
 * - `/导出`   → 会话行右键菜单「导出 Markdown」(且能导出**任意**会话,作用域更宽)
 *
 * 留下的两条**没有其他入口**,而斜杠菜单本身是可见的(输入框左侧「/」钮),
 * 点开就能看到它们 —— 所以既不算重复入口,也不会不可发现。
 */
const CHAT_COMMANDS: ChatCommand[] = [
  {
    name: '/存为笔记',
    desc: '分析本次对话,给出生成选项',
    keywords: ['笔记', '存', '整理', 'note'],
    run: () => {
      void saveAsNote()
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

// ---------------- 引用笔记(告诉 AI「在问什么」;检索仍会跑找关联笔记;弹层 UI 在 components/NotePicker.vue) ----------------

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

// ---------------- 输入草稿(按会话存 localStorage,刷新/切会话不丢) ----------------

const DRAFT_KEY_PREFIX = 'baleen-qa-draft::'
/** 进笔记选择器前的草稿:picker 复用输入框当筛选词,退出时还原 */
let draftBeforePicker = ''

/** 当前会话对应的草稿 key;新会话(未落库)用 'new' */
function draftKey(): string {
  return DRAFT_KEY_PREFIX + (currentSessionId.value === null ? 'new' : String(currentSessionId.value))
}

/** 恢复当前会话的输入草稿(挂载/openSession/startNew 时调用) */
function loadDraft(): void {
  let saved = ''
  try {
    saved = localStorage.getItem(draftKey()) ?? ''
  } catch {
    /* storage 禁用/满:跳过恢复,不影响主流程 */
  }
  input.value = saved
}

// 草稿随打字持久化;picker/slash 模式下输入框被复用为筛选/命令词,不存。
// ⚠️ slashOpen 不能当唯一判据:它在 @input 的 updateSlash 里才置位,而 v-model 的
// 赋值监听先于 updateSlash 执行,watch 的 flush 可能落在两者之间的窗口里(C2 探针
// 实测:fill 整词与逐字键入都复现)——窗口内 slashOpen 仍是 false,命令词会被当草稿
// 写进 localStorage,之后 /新对话 一执行,loadDraft() 又把命令词还原进输入框。
// 所以再加一道值判据:以 / 开头的输入就是命令词(updateSlash 对每个输入事件都会
// 重算 slashOpen = startsWith('/')),一律不存,不依赖置位时序。
watch(input, () => {
  if (notePicker.value || slashOpen.value || input.value.startsWith('/')) return
  try {
    localStorage.setItem(draftKey(), input.value)
  } catch {
    /* storage 禁用/满:跳过持久化 */
  }
})

function openNotePicker(): void {
  draftBeforePicker = input.value
  notePicker.value = true
  input.value = ''
  void loadPickerNotes('')
}

function closeNotePicker(): void {
  notePicker.value = false
  pickerNotes.value = []
  input.value = draftBeforePicker // 还原进 picker 前的草稿
}

function toggleNoteRef(note: NoteRef): void {
  const idx = referencedNotes.value.findIndex((r) => r.id === note.id)
  if (idx >= 0) referencedNotes.value.splice(idx, 1)
  else referencedNotes.value.push(note)
}

/** 从选择器选中一篇笔记:回填引用 chip → 关选择器(还原草稿) → 清输入。
 * 列表点击(NotePicker 的 select 事件)与 Enter 选第一篇走同一条路,顺序与拆分前一致。 */
function pickNote(note: NoteRef): void {
  toggleNoteRef(note)
  closeNotePicker()
  input.value = ''
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
      pickNote({ id: first.id, title: first.title })
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
  if (notePicker.value) return // 笔记选择器开着时输入的是筛选词,不再拉起命令菜单
  slashOpen.value = input.value.startsWith('/')
  slashActive.value = 0
}

function executeSlash(cmd: ChatCommand): void {
  // 先清输入再关菜单:此刻 slash 还开着,watch 不会把空串写进草稿(保住原本的问题草稿)
  input.value = ''
  closeSlash()
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
// agent 工具轮进度(status 事件,ADR 0010 v33):正文开始前显示「正在换词检索…/联网搜索…」
const agentStatus = ref('')

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

/** 会话级缓存命中率,分桶口径(token 加权 Σhit/Σ(hit+miss)):
 * - overall: 全部轮——真金白银的利用率
 * - kb: 纯库内轮——前缀设计质量的真实水平
 * - web: 联网轮——每轮现抓网页内容天然未命中,结构性偏低,不是缓存坏了
 * 联网判定:出处带 url(库内出处 url=null)或 agent trace 有 search_web 步骤
 * (搜了但最终没引网页也算联网轮——稀释发生在内容进上下文那一刻,不看引没引)。
 * 旧注释「联网轮双 0 不进分母」是决策树时代行为,agent 模式联网轮带 miss 进分母,
 * 这正是要分桶的原因。桶里没有任何带缓存数据的轮 → 该桶 null */
const cacheRateBuckets = computed<{ overall: number | null; kb: number | null; web: number | null }>(
  () => {
    const all = [0, 0]
    const kb = [0, 0]
    const web = [0, 0]
    for (const m of messages.value) {
      if (m.role !== 'assistant') continue
      const isWeb =
        m.citations.some((c) => c.url !== null) ||
        (m.agent_trace?.some((s) => s.tool === 'search_web') ?? false)
      const bucket = isWeb ? web : kb
      bucket[0] += m.cache_hit_tokens
      bucket[1] += m.cache_miss_tokens
      all[0] += m.cache_hit_tokens
      all[1] += m.cache_miss_tokens
    }
    const rate = (hit: number, mis: number) => (hit + mis > 0 ? hit / (hit + mis) : null)
    return { overall: rate(all[0], all[1]), kb: rate(kb[0], kb[1]), web: rate(web[0], web[1]) }
  },
)

const avgCacheRate = computed<number | null>(() => cacheRateBuckets.value.overall)

/** 两桶都有数据才显示分层——单桶会话(全联网/全库内)总数即桶数,无对比意义 */
const cacheRateSplit = computed<string | null>(() => {
  const { kb, web } = cacheRateBuckets.value
  if (kb === null || web === null) return null
  return `库内轮 ${(kb * 100).toFixed(1)}% · 联网轮 ${(web * 100).toFixed(1)}%`
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

/** 会话搜索:按标题过滤侧栏列表(会话多了能定位,不引入额外复杂度) */
const sessionQuery = ref('')
const filteredSessions = computed<QaSessionItem[]>(() => {
  const q = sessionQuery.value.trim().toLowerCase()
  if (q === '') return sessions.value
  return sessions.value.filter((s) => s.title.toLowerCase().includes(q))
})

/**
 * 会话行右侧的日期:M-D。会话的「最近活跃时间」后端没在列表里透出
 * (契约只有 created_at,改它属于 api.md 变更),所以先用创建日做区分。
 * 解析不出来就不显示 —— 宁可少一项,不显示 Invalid Date。
 */
function shortDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}/${d.getDate()}`
}

onBeforeUnmount(() => {
  stopThinkingTimer()
})

onMounted(() => {
  void refreshSessions()
  // URL 带了 ?session=N → 直接恢复那个会话(草稿在 openSession 里恢复)
  const sid = Number(route.query.session)
  if (Number.isFinite(sid) && sid > 0) void openSession(sid)
  else loadDraft() // 没指定会话 → 恢复新会话草稿
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
  loadDraft() // 新会话恢复自己的草稿
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
    loadDraft() // 恢复这个会话自己的输入草稿
    scrollBottom()
    // 历史消息里可能有 mermaid 图 / SVG 插图
    await nextTick()
    if (msgListEl.value !== null) {
      void renderMermaidIn(msgListEl.value)
      void renderMindmapsIn(msgListEl.value)
      void renderSvgsIn(msgListEl.value)
    }
  } catch (error) {
    // 旧会话可能已被保留策略清理(每用户只留最近 50 个;别的标签页里建新会话会顶掉它):
    // 落到新对话并给一句人话,别让用户对着「接口还没就绪」猜
    if (error instanceof ApiError && error.status === 404) {
      startNew()
      errorText.value = '这个会话已被清理(系统只保留最近 50 个会话),已开新对话。'
      return
    }
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
  const ok = await confirmDialog({
    title: '删除这个会话',
    body: '会话里的全部消息会一并删除,不可恢复。',
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteSession(id)
    if (currentSessionId.value === id) startNew()
    await refreshSessions()
  } catch (error) {
    errorText.value = describeApiError(error)
  }
}

// ---------------- 会话行右键菜单 + 行内重命名 ----------------

/**
 * 菜单只由右键唤起(主人拍板「只允许右键触发」),所以不需要 hover 显形的
 * 「⋯」钮 —— 行内除了标题和日期,不再有任何常驻控件。
 *
 * 位置用 fixed + 视口坐标:侧栏是 overflow-y:auto 的滚动容器,
 * 绝对定位会被裁掉;菜单挂到 body 上就不受容器裁剪。
 */
interface RowMenuState {
  open: boolean
  sessionId: number
  x: number
  y: number
}
const rowMenu = ref<RowMenuState>({ open: false, sessionId: 0, x: 0, y: 0 })
const rowMenuEl = ref<HTMLElement | null>(null)

/** 菜单尺寸(用于边界翻转);实际值在打开后量,这里给保守初值 */
const MENU_W = 168
const MENU_H = 132

function openRowMenu(event: MouseEvent, sessionId: number): void {
  event.preventDefault()
  // 正在回答时不许改会话元数据:删除/重命名都可能让当前流落到一个不存在的会话上
  if (sending.value) return
  const vw = window.innerWidth
  const vh = window.innerHeight
  // 贴光标弹出;右/下越界就翻到光标另一侧
  const x = event.clientX + MENU_W > vw ? event.clientX - MENU_W : event.clientX
  const y = event.clientY + MENU_H > vh ? event.clientY - MENU_H : event.clientY
  rowMenu.value = { open: true, sessionId, x: Math.max(8, x), y: Math.max(8, y) }
}

function closeRowMenu(): void {
  rowMenu.value = { ...rowMenu.value, open: false }
}

/** 行内重命名:null = 未在编辑 */
interface RenameState {
  sessionId: number
  value: string
}
const renaming = ref<RenameState | null>(null)
/**
 * 重命名输入框:它在 v-for 里,所以用函数式 ref 存元素 ——
 * 字符串 ref 在 v-for 内会被 Vue 收成数组,nextTick 拿到的不是元素。
 */
let renameInputEl: HTMLInputElement | null = null
function bindRenameInput(el: Element | ComponentPublicInstance | null): void {
  renameInputEl = el instanceof HTMLInputElement ? el : null
}
const renameError = ref('')

/** 超长即时标记:输入时就地反馈,不靠 toast 弹一句「太长了」 */
const renameTooLong = computed(
  () => renaming.value !== null && renaming.value.value.trim().length > SESSION_TITLE_MAX,
)

function startRename(s: QaSessionItem): void {
  closeRowMenu()
  renaming.value = { sessionId: s.id, value: s.title }
  renameError.value = ''
  void nextTick(() => {
    renameInputEl?.focus()
    renameInputEl?.select() // 全选:常见操作是整体换一个名字
  })
}

/** 双击标题进编辑(经典习语)。只在标题文字上生效,不抢日期区 */
function onTitleDblClick(s: QaSessionItem): void {
  if (sending.value) return
  startRename(s)
}

function cancelRename(): void {
  renaming.value = null
  renameError.value = ''
}

/** 确认重命名:成功就只改本地那一条(后端刻意不刷 updated_at,位次不动,无需重拉列表) */
async function commitRename(): Promise<void> {
  const state = renaming.value
  if (state === null) return
  const title = state.value.trim()
  if (title === '') {
    cancelRename() // 清空等于放弃,不发请求
    return
  }
  if (title.length > SESSION_TITLE_MAX) return // 超长:边框已红,不提交
  const target = sessions.value.find((s) => s.id === state.sessionId)
  if (target !== undefined && target.title === title) {
    cancelRename() // 没改就不发请求
    return
  }
  try {
    const updated = await renameSession(state.sessionId, title)
    const row = sessions.value.find((s) => s.id === updated.id)
    if (row !== undefined) row.title = updated.title
    cancelRename()
  } catch (error) {
    renameError.value = describeApiError(error)
  }
}

function onRenameKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter') {
    event.preventDefault()
    void commitRename()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    cancelRename()
  }
}

/** 点菜单外部 / 按 Esc 关菜单(菜单项自己 stop 冒泡) */
function onDocPointerDown(event: PointerEvent): void {
  if (!rowMenu.value.open) return
  const menu = rowMenuEl.value
  if (menu !== null && event.target instanceof Node && menu.contains(event.target)) return
  closeRowMenu()
}

function onDocKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && rowMenu.value.open) closeRowMenu()
}

watch(
  () => rowMenu.value.open,
  (open) => {
    if (open) {
      document.addEventListener('pointerdown', onDocPointerDown, true)
      document.addEventListener('keydown', onDocKeydown)
    } else {
      document.removeEventListener('pointerdown', onDocPointerDown, true)
      document.removeEventListener('keydown', onDocKeydown)
    }
  },
)

/**
 * 会话标题悬停滚动:超宽时向左匀速滑到末尾再回位,方便看全名。
 * 与书架题签同一套做法(量出溢出像素写进 CSS 变量),只是这里挂在会话行上。
 */
function checkSessionTitleOverflow(el: HTMLElement): void {
  const overflowPx = el.scrollWidth - el.clientWidth
  const tracked = el.classList.contains('session-title--overflow')
  if (overflowPx > 2) {
    if (!tracked) el.classList.add('session-title--overflow')
    el.style.setProperty('--title-shift', `${-overflowPx}px`)
    // 约 50px/s(会话名比书封短,稍快一点才不至于等太久),最少 2.5s
    el.style.setProperty('--title-scroll-dur', `${Math.max(2.5, Math.round(el.scrollWidth / 50))}s`)
  } else if (tracked) {
    el.classList.remove('session-title--overflow')
  }
}

/** 本地指令:逐个标题量宽,只有真溢出的才允许滑动 */
const vSessionTitleScroll: Directive<HTMLElement> = {
  mounted: checkSessionTitleOverflow,
  updated: checkSessionTitleOverflow,
}

// ---- 菜单三项:菜单只记 sessionId,按 id 找行再分发(菜单自己不持有整个会话对象) ----

function startRenameById(sessionId: number): void {
  const target = sessions.value.find((s) => s.id === sessionId)
  if (target !== undefined) startRename(target)
}

function exportById(sessionId: number): void {
  closeRowMenu()
  void exportSession(sessionId)
}

function deleteById(sessionId: number): void {
  closeRowMenu()
  void removeSession(sessionId)
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
  agentStatus.value = ''
  startThinkingTimer()
  // 引用/附件随问题一起出发:捕获进当轮消息(随气泡上屏)、输入框立刻清空,不等 AI 回完。
  // 流没建立起来时(catch 分支)再原样还回来。
  const sentExtras: SentExtras = {
    notes: referencedNotes.value.map((r) => ({ id: r.id, title: r.title })),
    files: attachments.value.map((a) => a.name),
    images: imageAttachments.value.map((i) => ({ name: i.name, data_url: i.data_url })),
  }
  const refNoteIds = referencedNotes.value.map((r) => r.id)
  const attachmentsPayload = attachments.value.map((a) => ({ name: a.name, text: a.text }))
  const imagesPayload = imageAttachments.value.map((i) => ({
    name: i.name,
    mime: i.mime,
    data_url: i.data_url,
  }))
  referencedNotes.value = [] // 引用随问题消耗掉
  attachments.value = [] // 附件随问题消耗掉
  imageAttachments.value = [] // 图片附件随问题消耗掉
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
    cache_hit_tokens: 0,
    cache_miss_tokens: 0,
    sent_extras: sentExtras,
  })
  input.value = ''
  scrollBottom()
  let streamingIdx: number | null = null
  let usedSearchQuery = ''
  pendingModel.value = null
  try {
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
          // 后端的 user_message 没有 chips 信息,把本地捕获的带上,气泡里继续显示
          if (idx !== -1)
            messages.value.splice(idx, 1, { ...meta.user_message, sent_extras: sentExtras })
          messages.value.push({
            id: -1,
            role: 'assistant',
            content: '',
            citations: [],
            model: null,
            cost_tokens: 0,
            cache_hit_rate: null,
            cache_hit_tokens: 0,
            cache_miss_tokens: 0,
            search_query: meta.search_query,
          })
          streamingIdx = messages.value.length - 1
          scrollBottom()
        },
        onDelta: (t) => {
          if (!streamingStarted.value) {
            streamingStarted.value = true // 正文开始,不再显示「思考中」
            agentStatus.value = ''
            stopThinkingTimer()
          }
          if (streamingIdx !== null && messages.value[streamingIdx] !== undefined) {
            messages.value[streamingIdx].content += t
            scrollBottom()
          }
        },
        onStatus: (t) => {
          // agent 工具轮进度:替换上一条(同一时刻只有一步在跑)
          agentStatus.value = t
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
              void renderMindmapsIn(msgListEl.value)
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
  } catch (error) {
    // 流没建立起来(网络/后端未起):撤下乐观消息与空气泡,还原文输入和 chips
    const idx = messages.value.findIndex((m) => m.id === tempId)
    if (idx !== -1) messages.value.splice(idx, 1)
    if (streamingIdx !== null && messages.value[streamingIdx]?.content === '') {
      messages.value.splice(streamingIdx, 1)
    }
    const desc = describeApiError(error)
    // 发问的会话已被保留策略清理(旧标签页里的闲置会话会被顶掉):开新对话承接,
    // 提问与引用/附件原样还回输入框,重发即可
    const sessionGone = error instanceof Error && desc.includes('会话不存在')
    if (sessionGone) startNew()
    input.value = text
    referencedNotes.value = sentExtras.notes.map((n) => ({ ...n }))
    attachments.value = attachmentsPayload.map((a) => ({ ...a }))
    imageAttachments.value = imagesPayload.map((im) => ({ ...im }))
    errorText.value = sessionGone
      ? '这个会话已被清理(系统只保留最近 50 个),已开新对话;提问还在输入框,重发即可。'
      : desc
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
      cache_hit_tokens: 0,
      cache_miss_tokens: 0,
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

/** 答案正文点击委托:点 [n]/[W n] 徽章 → 直接跳到该出处(库内笔记本页打开,联网出处开新窗) */
function onAnswerClick(m: ChatMessage, event: MouseEvent): void {
  const refEl = (event.target as HTMLElement | null)?.closest('.cite-ref')
  if (!(refEl instanceof HTMLElement)) return
  const raw = refEl.dataset.cite ?? ''
  if (raw === '') return
  const n = Number(raw.startsWith('W') ? raw.slice(1) : raw)
  if (!Number.isFinite(n)) return
  const citation = m.citations.find((c) => c.n === n)
  if (citation !== undefined) openCitation(citation)
}
</script>

<template>
  <section class="page qa-page">
    <div class="qa-layout manuscript">
      <aside class="session-side">
        <div class="session-side-inner">
          <button class="btn btn--primary new-btn" type="button" @click="startNew">+ 新对话</button>
          <input
            v-model="sessionQuery"
            class="input session-search"
            type="text"
            placeholder="搜索会话…"
            aria-label="搜索会话"
          />
          <ul v-if="filteredSessions.length > 0" class="session-list">
            <li
              v-for="s in filteredSessions"
              :key="s.id"
              class="session-item"
              :data-session-id="s.id"
              :class="{
                'session-item--active': s.id === currentSessionId,
                'session-item--renaming': renaming?.sessionId === s.id,
              }"
              @contextmenu="openRowMenu($event, s.id)"
            >
              <!-- 重命名:标题位原地变输入框 -->
              <input
                v-if="renaming?.sessionId === s.id"
                :ref="bindRenameInput"
                v-model="renaming.value"
                class="session-rename-input"
                :class="{ 'session-rename-input--over': renameTooLong }"
                type="text"
                :maxlength="SESSION_TITLE_MAX + 20"
                :aria-label="`重命名会话,最多 ${SESSION_TITLE_MAX} 字`"
                @keydown="onRenameKeydown"
                @blur="commitRename"
                @contextmenu.stop
              />
              <button
                v-else
                class="session-open"
                type="button"
                :title="s.title"
                @click="openSession(s.id)"
                @dblclick.prevent="onTitleDblClick(s)"
              >
                <!-- 外筒负责裁切与宽度,内层负责滑动(与书架题签同一套 v-title-scroll 做法) -->
                <span v-session-title-scroll class="session-title">
                  <span class="session-title-text">{{ s.title }}</span>
                </span>
                <!-- 日期:同名会话(实测 44 条里 11 条同名)靠它区分;只到日,不占宽度 -->
                <span class="session-date">{{ shortDate(s.created_at) }}</span>
              </button>
            </li>
          </ul>
          <p v-else class="muted side-empty">
            {{ sessions.length === 0 ? '还没有历史会话' : '没有匹配的会话' }}
          </p>
          <!-- 重命名失败提示:必须放在 v-if/v-else 整组之外 ——
               夹在两者之间会把它们拆成两个独立条件,列表和空态就会同时渲染 -->
          <p v-if="renameError !== ''" class="note note--error session-rename-error">
            {{ renameError }}
          </p>
        </div>
      </aside>

      <div class="chat-main" @dragover.prevent @drop.prevent="onChatDrop">
        <!-- 新对话空态:只留一句「问什么」。操作入口不进这里 ——
             命令菜单靠输入框里的「/」钮、附件靠 📎,都在下面看得见 -->
        <div v-if="messages.length === 0 && !loadingSession" class="qa-empty">
          <p class="qa-empty-title">问点什么吧</p>
          <p class="qa-empty-sub">关于笔记库的问题,或任何想整理的资料</p>
        </div>
        <div v-else ref="msgListEl" class="msg-weave">
          <!-- 单列对话流:问答同列按时间下行;问右对齐(朱砂缘),答通栏文章式 -->
          <div
            v-for="(m, i) in messages"
            :key="m.id"
            class="leaf"
            :class="m.role === 'user' ? 'leaf--note' : 'leaf--answer'"
          >
            <!-- 眉批:你的提问(衬线 + 朱砂问章),发出时带走的引用/附件跟在批注后 -->
            <template v-if="m.role === 'user'">
              <p class="note-text"><span class="note-seal" aria-hidden="true">问</span>{{ m.content }}</p>
              <div
                v-if="m.sent_extras !== undefined && (m.sent_extras.notes.length > 0 || m.sent_extras.files.length > 0 || m.sent_extras.images.length > 0)"
                class="note-extras"
              >
                <span
                  v-for="n in m.sent_extras.notes"
                  :key="`n${n.id}`"
                  class="note-chip"
                  :title="`引用了《${n.title}》`"
                >
                  《{{ n.title }}》
                </span>
                <span
                  v-for="f in m.sent_extras.files"
                  :key="`f${f}`"
                  class="note-chip"
                  :title="`附带了文件 ${f}`"
                >
                  {{ f }}
                </span>
                <span
                  v-for="im in m.sent_extras.images"
                  :key="`i${im.name}`"
                  class="note-chip note-chip--image"
                  :title="`附带了图片 ${im.name}`"
                >
                  <img class="note-chip-thumb" :src="im.data_url" alt="" />
                  {{ im.name }}
                </span>
              </div>
            </template>
            <!-- 正文:AI 回答文章式排版(无气泡),文末检索词 + 出处折叠 -->
            <template v-else>
              <p v-if="m.content === ''" class="muted leaf-waiting">正在落墨…</p>
              <template v-else>
                <p class="leaf-head"><span class="answer-seal" aria-hidden="true">答</span></p>
                <!-- 渲染器内部已先 HTML 转义,输出只含自己拼的标签,不是原文直接插进 DOM;
                     点击委托:点 [n]/[W n] 徽章 → 直接跳该出处 -->
                <article class="md answer-md" v-html="rendered[i]" @click="onAnswerClick(m, $event)" />
              </template>
              <p v-if="m.search_query" class="search-hint muted">
                检索词:{{ m.search_query }}
              </p>
              <details v-if="m.citations.length > 0" class="cite-fold" :open="i === messages.length - 1">
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
                    <span v-if="c.heading_path !== null" class="cite-path" :title="c.heading_path">
                      {{ c.heading_path }}
                    </span>
                    <span v-if="c.url !== null" class="cite-path" :title="prettyUrl(c.url)">
                      {{ prettyUrl(c.url) }}
                    </span>
                  </li>
                </ul>
              </details>
            </template>
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

        <!-- 思考中:复用输入区上方的状态行,不再往正文流插临时占位 -->
        <p v-if="sending && !streamingStarted" class="thinking-strip muted" aria-live="polite">
          <span class="thinking">思考中</span>
          <span class="thinking-dots" aria-hidden="true">…</span>
          <span v-if="thinkingSec > 0" class="thinking-sec">{{ thinkingSec }}s</span>
          <span v-if="pendingModel !== null" class="thinking-model">· {{ pendingModel }}</span>
          <span v-if="agentStatus !== ''" class="thinking-status">{{ agentStatus }}</span>
          <span v-if="thinkingSec >= 15" class="thinking-hint">（模型较慢，最长可能需要 2 分钟）</span>
        </p>

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
          <!-- / 命令菜单(组件):开关/过滤/亮项在本页,选择与悬停经事件回传 -->
          <SlashMenu
            :open="slashOpen"
            :commands="slashMatches"
            :active="slashActive"
            @select="executeSlash"
            @hover="slashActive = $event"
          />
          <!-- /检索 的笔记选择器(组件):候选/筛选词在本页,关闭与选择经事件回传;
               全屏遮罩兜底「点外部也能退出」,菜单本体盖在遮罩之上 -->
          <NotePicker
            :open="notePicker"
            :notes="pickerNotes"
            :loading="pickerLoading"
            :query="input"
            @close="closeNotePicker"
            @select="pickNote"
          />
          <!-- 输入壳:chips 和文字同框(用户视角 = 文件圆角框嵌在句子里);
               壳本身长得像 input,真输入框无边框透明藏在里面 -->
          <div class="input-shell" @click="focusQaInput">
            <!-- 已引用的笔记 chips(黛青 = 库内笔记) -->
            <div v-if="referencedNotes.length > 0" class="ref-chips">
              <span
                v-for="r in referencedNotes"
                :key="r.id"
                class="ref-chip"
                :title="`取消引用《${r.title}》`"
              >
                《{{ r.title }}》
                <button class="ref-chip-x" type="button" aria-label="取消引用" @click="removeNoteRef(r.id)">
                  ×
                </button>
              </span>
            </div>
            <!-- 已挂的文档附件 chips(灰 = 临时参考文件,不入库;随提问消耗) -->
            <div v-if="attachments.length > 0" class="ref-chips">
              <span
                v-for="(a, idx) in attachments"
                :key="a.name"
                class="ref-chip ref-chip--attach"
                :title="`取消附件《${a.name}》`"
              >
                {{ a.name }}
                <span
                  v-if="a.analyzed"
                  class="chip-analyzed"
                  title="大文件已由 AI 分析提炼,细节可能省略"
                >
                  已分析
                </span>
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
            <!-- 命令菜单入口:空输入时露出的「/」钮。点了等于手打一个 /,
                 菜单随即弹出 —— 命令是「看得见的东西」,不用一行提示教怎么打字 -->
            <button
              v-if="input === '' && !sending"
              class="slash-trigger"
              type="button"
              title="命令"
              aria-label="打开命令菜单"
              @click.stop="openSlashFromButton"
            >
              /
            </button>
            <input
              ref="qaInputEl"
              v-model="input"
              class="qa-input"
              type="text"
              :placeholder="notePicker ? '输入标题筛选,点击上方笔记引用(Esc 退出)' : '问点什么…'"
              aria-label="问题"
              :disabled="sending"
              @input="updateSlash(); onPickerInput()"
              @keydown="onInputKeydown"
            />
            <!-- 附件入口:同住输入壳内,和 / 钮一起构成「输入框自带的两个小工具」;
                 壳外只剩发送键,三个按钮不再散落三处 -->
            <button
              class="attach-btn"
              type="button"
              :disabled="sending"
              title="附加参考文件/图片给 AI 看,不入库"
              aria-label="附加参考文件或图片"
              @click.stop="attachFileInput?.click()"
            >
              📎
            </button>
          </div>
          <button class="btn btn--primary" type="submit" :disabled="sending">发送</button>
        </form>

        <p class="cache-status muted" aria-live="polite">
          <template v-if="displayModel !== null">
            <span class="model-tag" title="当前使用的模型">{{ displayModel }}</span>
            <span class="status-sep">·</span>
          </template>
          平均缓存命中率:
          <span v-if="avgCacheRate !== null" class="cache-value">
            {{ (avgCacheRate * 100).toFixed(1) }}%
          </span>
          <span v-else-if="hasAssistantMessage">—(当前模型未返回缓存数据)</span>
          <span v-else>—</span>
          <span
            v-if="cacheRateSplit !== null"
            class="cache-split"
            title="联网轮每轮现抓网页内容,天然未命中,偏低是结构性现象;库内轮才反映前缀设计的真实缓存水平"
          >
            ({{ cacheRateSplit }})
          </span>
        </p>
      </div>

    </div>

    <!-- 会话行右键菜单:fixed 定位(侧栏是滚动容器,绝对定位会被裁)。
         三项:重命名 / 导出 Markdown / 删除,删除单独一组靠分隔线隔开 -->
    <div
      v-if="rowMenu.open"
      ref="rowMenuEl"
      class="row-menu card"
      role="menu"
      :style="{ left: `${rowMenu.x}px`, top: `${rowMenu.y}px` }"
      @contextmenu.prevent
    >
      <button class="row-menu-item" type="button" role="menuitem" @click="startRenameById(rowMenu.sessionId)">
        <span class="row-menu-ico" aria-hidden="true">✎</span>
        重命名
      </button>
      <button
        class="row-menu-item"
        type="button"
        role="menuitem"
        @click="exportById(rowMenu.sessionId)"
      >
        <span class="row-menu-ico" aria-hidden="true">↓</span>
        导出 Markdown
      </button>
      <div class="row-menu-sep" aria-hidden="true"></div>
      <button
        class="row-menu-item row-menu-item--danger"
        type="button"
        role="menuitem"
        @click="deleteById(rowMenu.sessionId)"
      >
        <span class="row-menu-ico" aria-hidden="true">×</span>
        删除
      </button>
    </div>
  </section>
</template>

<style scoped>
/* 出血形态根:撑满 stage--bleed 给的高度链 */
.qa-page {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}

.qa-layout {
  display: flex;
  flex: 1;
  min-height: 0;
  align-items: stretch;
  gap: 20px;
}

/* ---- 眉批手帐:整页一张摊满视口的宣纸,不再有边框/阴影的「盒」感 ---- */
.manuscript {
  display: flex;
  flex: 1;
  min-height: 0;
  padding: 20px 26px 16px;
  background: var(--panel);
}

/* 书签绳外壳:内容不参与行高(绝对铺满),行高只由右侧纸面定——
   两栏像素级同高;会话再多也只在侧栏内部滚,不再把整页拉长。
   宽度 176→248:实测 176 下 44 条会话有 38 条被截断(86%)。
   配合导出/删除改绝对定位(不再占宽),标题可用宽度 99→189px,
   常见长度的标题能完整显示;问答页已是 1440 宽档,这一栏不挤对话列。 */
.session-side {
  position: relative;
  flex-shrink: 0;
  width: 248px;
}

.session-side-inner {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
}

/* 新对话:浅黛青(不用全局墨底主钮——纸面上大面积黑太刺眼) */
.new-btn {
  width: 100%;
  margin-bottom: 10px;
  border-color: rgba(61, 90, 115, 0.4);
  background: var(--accent-soft);
  color: var(--accent-ink);
}

.new-btn:hover:not(:disabled) {
  border-color: var(--accent);
  background: rgba(61, 90, 115, 0.14);
}

.side-empty {
  font-size: 13px;
}

/* 会话列表 = 书签绳:左侧一道竖虚线作绳,每条会话是挂在其上的签,
   签结小圆点,当前会话系朱砂结;不再是卡片列表 */
.session-list {
  display: flex;
  list-style: none;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 0 6px 14px;
  border-left: 1px dashed var(--line-strong);
  /* 细墨滚动条:换掉 Windows 原生粗轨道(带 ▲▼ 箭头,像把内容遮了一截) */
  scrollbar-width: thin;
  scrollbar-color: var(--line-strong) transparent;
}

.session-list::-webkit-scrollbar {
  width: 6px;
}

.session-list::-webkit-scrollbar-track {
  background: transparent;
}

.session-list::-webkit-scrollbar-thumb {
  border-radius: 3px;
  background: var(--line-strong);
}

.session-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 2px;
}

/* 签结 */
.session-item::before {
  content: '';
  position: absolute;
  left: -17.5px;
  top: 50%;
  width: 7px;
  height: 7px;
  border: 1.5px solid var(--line-strong);
  border-radius: 50%;
  background: var(--panel);
  transform: translateY(-50%);
}

/* 当前会话:朱砂结 + 提字 */
.session-item--active::before {
  border-color: var(--seal);
  background: var(--seal);
}

.session-item--active .session-open {
  color: var(--ink);
  font-weight: 600;
}

.session-open {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: baseline;
  gap: 6px;
  overflow: hidden;
  padding: 6px 4px 6px 6px;
  border: none;
  background: none;
  color: var(--ink-2);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}

/* 标题:外筒裁切,内层滑动。
   超宽时悬停向左匀速滑到末尾再回位,方便看全名(与书架题签同一套指令做法);
   只有真溢出的行才会被加上 --overflow 类,短标题不会乱动。 */
.session-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  /* 右缘 10px 渐隐:提示「后面还有字」,比省略号更安静 */
  mask-image: linear-gradient(90deg, #000 calc(100% - 10px), transparent);
  -webkit-mask-image: linear-gradient(90deg, #000 calc(100% - 10px), transparent);
}

.session-title-text {
  display: inline-block;
  padding-right: 1.2em; /* 滑到尽头时末字不贴边 */
  white-space: nowrap;
}

.session-title--overflow:hover .session-title-text {
  animation: session-title-scroll var(--title-scroll-dur, 4s) linear infinite;
}

@keyframes session-title-scroll {
  0%,
  10% {
    transform: translateX(0);
  }

  88%,
  100% {
    transform: translateX(var(--title-shift, -30%));
  }
}

.session-date {
  flex-shrink: 0;
  color: var(--ink-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.session-open:hover {
  color: var(--accent-ink);
}

/* ---------- 行内重命名 ---------- */

.session-rename-input {
  width: 100%;
  min-width: 0;
  padding: 4px 8px;
  border: 1px solid var(--accent);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
  font-family: inherit;
  font-size: 13px;
  outline: none;
  box-shadow: 0 0 0 3px rgba(61, 90, 115, 0.12);
}

/* 超长:边框转朱砂 —— 就地反馈,不弹 toast 说「太长了」 */
.session-rename-input--over {
  border-color: var(--seal);
  box-shadow: 0 0 0 3px rgba(176, 58, 46, 0.14);
}

.session-rename-error {
  margin-top: 6px;
  font-size: 12px;
}

/* ---------- 右键菜单:一枚贴在书签上的便签 ---------- */

.row-menu {
  position: fixed;
  /* 层级:顶栏 10 / 灯箱 1000 / 纸篓 1100 / 飞纸 1200 / 确认框 1300。
     菜单要压过页面内容与顶栏,但低于灯箱/纸篓这类真正的模态层 */
  z-index: 900;
  min-width: 168px;
  padding: 5px;
  box-shadow: var(--shadow-lift);
  animation: row-menu-in 0.12s ease;
}

@keyframes row-menu-in {
  from {
    opacity: 0;
    transform: translateY(-3px);
  }

  to {
    opacity: 1;
    transform: none;
  }
}

.row-menu-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  border: none;
  border-radius: 7px;
  background: none;
  color: var(--ink);
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  text-align: left;
}

.row-menu-item:hover {
  background: var(--bg-soft);
}

/* 图标固定 14px 宽,中英混排时三项文字左边对齐 */
.row-menu-ico {
  display: inline-flex;
  width: 14px;
  flex-shrink: 0;
  justify-content: center;
  color: var(--ink-3);
  font-size: 13px;
}

.row-menu-item:hover .row-menu-ico {
  color: var(--accent-ink);
}

.row-menu-sep {
  height: 1px;
  margin: 4px 8px;
  background: var(--line);
}

/* 删除:平时朱砂字,hover 才铺底 —— 危险但在菜单里不必一直喊 */
.row-menu-item--danger {
  color: var(--seal);
}

.row-menu-item--danger .row-menu-ico {
  color: var(--seal);
  font-size: 15px;
}

.row-menu-item--danger:hover {
  background: var(--seal-soft);
}

/* ---- 新对话空态(高度随纸面自适应) ---- */
.qa-empty {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  text-align: center;
}

.qa-empty-title {
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.qa-empty-sub {
  color: var(--ink-2);
  font-size: 13.5px;
}

/* 对话列限宽居中:长答案行宽 ~760px 是阅读舒适区,列在侧栏之外的剩余空间里居中,
   两侧对称留白(ChatGPT 式);空态/输入框/缓存行都随列同宽 */
.chat-main {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
}

/* 会话搜索框:压在「+ 新对话」和书签绳之间,占位小、不抢戏 */
.session-search {
  margin-bottom: 10px;
  padding: 6px 10px;
  font-size: 13px;
}

/* 单列对话流:问答同列按时间垂直下行,通栏放宽、两侧只留 24px 呼吸边距;
   高度吃满纸面剩余并内部滚动,输入栏钉在视口底部 */
.msg-weave {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  gap: 26px;
  padding: 4px 24px;
  overflow-y: auto;
  /* 与书签绳同款细墨滚动条 */
  scrollbar-width: thin;
  scrollbar-color: var(--line-strong) transparent;
}

.msg-weave::-webkit-scrollbar {
  width: 6px;
}

.msg-weave::-webkit-scrollbar-track {
  background: transparent;
}

.msg-weave::-webkit-scrollbar-thumb {
  border-radius: 3px;
  background: var(--line-strong);
}

.leaf {
  min-width: 0;
}

/* ---- 用户提问:右对齐轻量块,衬线小字 + 右缘一道朱砂细线(眉批语言收进单列) ---- */
.leaf--note {
  align-self: flex-end;
  max-width: 82%;
  padding: 2px 12px 2px 0;
  border-right: 2px solid rgba(176, 58, 46, 0.45);
}

.note-seal {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-right: 7px;
  border-radius: 50%;
  background: var(--seal);
  color: #fff;
  font-size: 11px;
  line-height: 1;
  vertical-align: 1px;
}

.note-text {
  margin: 0;
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 14.5px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 提问里随行的引用/附件 chips:浅黛青小牌,随块右对齐 */
.note-extras {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 5px;
  margin-top: 8px;
}

.note-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 2px 8px;
  overflow: hidden;
  border: 1px solid rgba(61, 90, 115, 0.3);
  border-radius: 7px;
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.note-chip-thumb {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  object-fit: cover;
  border-radius: 4px;
}

/* ---- 正文:AI 回答,无框文章直接落纸,首行盖墨色「答」章与朱砂「问」章对仗 ---- */
.leaf-head {
  margin: 0 0 6px;
}

.answer-seal {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--ink);
  color: var(--panel);
  font-size: 11px;
}

.leaf-waiting {
  font-size: 13.5px;
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
  background: var(--bg-soft);
  cursor: pointer;
  font-size: 13px;
}

.cite-item:hover {
  background: var(--line);
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

/* 正文引用徽章可点:点击经 article 委托切出处坞(徽章外观沿用全局 .cite-ref) */
.answer-md :deep(.cite-ref) {
  cursor: pointer;
}

/* 浅纸墨槽:底部通栏输入,浅纸色凹槽(不再是大面积黑),聚焦时黛青描边;
   与对话列同宽(两侧同留 24px);chips 用 display:contents 直接成为壳的 flex 项,和输入文字一起换行 */
.chat-input {
  display: flex;
  gap: 10px;
  margin: 14px 24px 0;
}

.chat-input .btn {
  flex-shrink: 0;
  white-space: nowrap;
}

.input-shell {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 14px;
  background: var(--bg-soft);
  cursor: text;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease;
}

.input-shell:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(61, 90, 115, 0.1);
}

/* 墨槽里的真输入框:无边框透明 */
.qa-input {
  flex: 1 1 140px;
  min-width: 140px;
  padding: 6px 4px;
  border: none;
  background: transparent;
  color: var(--ink);
  font-family: inherit;
  font-size: 14px;
  outline: none;
}

/* 命令入口:空输入时露出的「/」钮,放在输入文字前。形如一枚小墨印,
   悬停转黛青 —— 它替代了原先空态里那三行「教你怎么打字」的说明 */
.slash-trigger {
  display: flex;
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink-2);
  cursor: pointer;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  transition:
    border-color 0.15s ease,
    color 0.15s ease,
    background 0.15s ease;
}

.slash-trigger:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-ink);
}

.qa-input::placeholder {
  color: var(--ink-3);
}

.qa-input:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* 📎 附件按钮:低调图标键,与发送键区分 */
/* 附件入口:住在输入壳里(与 / 钮同列),所以不是 .btn 那种带边框的独立键,
   而是一个无框图标位 —— 壳的边框已经表达了「这里是一个输入框」 */
.attach-btn {
  display: flex;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  transition:
    color 0.15s ease,
    background 0.15s ease;
}

.attach-btn:hover:not(:disabled) {
  background: var(--bg-soft);
  color: var(--accent-ink);
}

.attach-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cache-status {
  margin: 8px 24px 0;
  font-size: 12.5px;
}

.cache-value {
  color: var(--accent-ink);
  font-weight: 650;
}

/* 思考状态行:钉在输入区上方(与缓存状态行同区),复用固定区空间,不占正文 */
.thinking-strip {
  margin: 10px 24px 0;
  font-size: 13px;
}

/* 思考中:秒数 + 久等提示 */
.thinking-sec {
  margin-left: 6px;
  color: var(--ink-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.thinking-hint {
  margin-left: 6px;
  color: var(--ink-3);
  font-size: 12px;
}

.thinking-model {
  margin-left: 4px;
  color: var(--ink-3);
  font-family: 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
}

/* agent 工具轮进度(思考行内,随 status 事件更新) */
.thinking-status {
  margin-left: 6px;
  color: var(--ink-3);
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
  margin: 14px 24px 0;
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

/* ---- 斜杠命令菜单 / 笔记选择器:弹层样式已随 UI 拆进 components/SlashMenu.vue 与 NotePicker.vue ---- */
.chat-input--anchor {
  position: relative;
}

/* ---- 引用笔记 / 附件 chips(嵌在墨槽里:浅色小牌) ---- */
.ref-chips {
  display: contents;
}

.ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 240px;
  padding: 2px 8px;
  overflow: hidden;
  border: 1px solid rgba(61, 90, 115, 0.38);
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 附件 chip:用中性色调与「引用笔记」区分(它是临时参考,不是库内笔记) */
.ref-chip--attach {
  border-color: var(--line-strong);
  background: var(--panel);
  color: var(--ink-2);
}

/* 「已分析」小标:大文件经后端 AI 分析提炼的标记 */
.chip-analyzed {
  font-size: 11px;
  color: var(--accent-ink);
  opacity: 0.85;
  white-space: nowrap;
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
