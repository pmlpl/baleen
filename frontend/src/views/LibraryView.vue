<script setup lang="ts">
/**
 * 笔记库:双视图,每页 12 条(6 列 × 2 行,一屏放下),多了翻页。
 * - 书架视图:书卡网格(列数自适应,锁 5:7 书的比例),横排题签,管"逛"的氛围
 * - 列表视图:全名行条目,质量/段数/日期齐全,管查找和管理
 * 右上角切换,localStorage 记住(默认书架)。
 *
 * GET /api/notes?q=&limit=&offset= —— 搜索框输入防抖 300ms 才发请求;
 * 翻页/搜索都通过 reload() 重拉,失败给人话提示不白屏。
 */
import { computed, onActivated, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { Directive } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { deleteNote, listNotes } from '../api/notes'
import type { NoteListItem } from '../api/notes'
import { createSource, getSource, MAX_UPLOAD_BYTES } from '../api/sources'
import type { SourceDetail } from '../api/sources'
import { useTrashStore } from '../stores/trash'
import { flyToBasket } from '../utils/flyPaper'
import { spineColor } from '../utils/spine'

/** 输入停下来 300ms 才查(任务书要求的防抖值) */
const DEBOUNCE_MS = 300
/** 每页 12 条 = 书架 6 列 × 2 行(主人 2026-11-25:「一页两行即可」)——
 *  一屏刚好放下,不用滚动;列表视图同样一页 12 行。 */
const PAGE_SIZE = 12

const keyword = ref('')
const appliedKeyword = ref('')
const notes = ref<NoteListItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const errorText = ref('')

/**
 * 纸篓(store 只管视觉:飞纸动画期间卡片淡出 + 角标数量缓存)。
 *
 * v37 起「哪些笔记在纸篓里」由后端决定(notes.status='archived'),而服务端列表
 * 本来就只返回 active —— 所以书架**不需要再做任何本地过滤**:
 * 移入纸篓后再 load(),那篇自然不在了。
 */
const trash = useTrashStore()
/** 列表与计数直接用服务端结果(v37:archived 不会出现在 /api/notes 里) */
const visibleNotes = computed(() => notes.value)
const visibleTotal = computed(() => total.value)

// ---------------- 双视图:书卡管氛围,列表管查找 ----------------

type ShelfView = 'books' | 'list'
const SHELF_VIEW_KEY = 'baleen-shelf-view'
const view = ref<ShelfView>('books')
try {
  const saved = localStorage.getItem(SHELF_VIEW_KEY)
  if (saved === 'books' || saved === 'list') view.value = saved
} catch {
  /* storage 禁用/满:默认书架 */
}

function setView(next: ShelfView): void {
  view.value = next
  try {
    localStorage.setItem(SHELF_VIEW_KEY, next)
  } catch {
    /* 记不住就当次会话有效 */
  }
}

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

// 窗口获得焦点 → 自动重拉书架：其他标签页（视频笔记导入等）产生的新笔记不用手动刷新
function refreshOnFocus(): void {
  if (!importing.value) void load()
}
onMounted(() => window.addEventListener('focus', refreshOnFocus))
onBeforeUnmount(() => window.removeEventListener('focus', refreshOnFocus))
// keep-alive 缓存下,从详情页删完/改完跳回书架时 onMounted 不再触发 → 列表会 stale。
// onActivated 兜底:切回书架时自动校准(首次激活跳过,避免与 watch immediate 重复加载)。
// 同时刷纸篓数量 —— 在纸篓弹窗里放回/彻底删之后切回来,角标要跟上。
let activatedOnce = false
onActivated(() => {
  void trash.refreshCount()
  if (activatedOnce) void load()
  activatedOnce = true
})

function goPage(next: number): void {
  const clamped = Math.min(Math.max(1, next), pageCount.value)
  if (clamped !== page.value) page.value = clamped
}

/** 页码列表:页数多时只显示当前页附近 + 首末页(用 null 表示省略号) */
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

/** ---- 批量移入纸篓:点「批量删除」进入选择模式,点书卡/行选中或取消,选好统一入篓 ---- */
const selecting = ref(false)
const selectedIds = ref<Set<number>>(new Set())
const batchDeleting = ref(false)

function toggleBatch(): void {
  if (selecting.value) {
    selecting.value = false
    selectedIds.value.clear()
  } else {
    selecting.value = true
  }
}

function toggleSelect(id: number): void {
  if (selectedIds.value.has(id)) selectedIds.value.delete(id)
  else selectedIds.value.add(id)
}

/** 批量模式下点书卡/行 = 选中切换,不进笔记;平时点 = 正常跳转。
 *  必须挂在捕获阶段(@click.capture):RouterLink 在 a 元素自己的 click 监听里
 *  就同步 router.push 了,冒泡阶段再 preventDefault 为时已晚;
 *  捕获先拦,vue-router 的 guardEvent 见 defaultPrevented 会放弃跳转 */
function onCardClick(event: MouseEvent, id: number): void {
  if (!selecting.value) return
  event.preventDefault()
  toggleSelect(id)
}

/** 正在拖拽的书:原位留一道浅影,像书被从架上拎了起来 */
const draggingId = ref<number | null>(null)

/** 拖书入纸篓:dragstart 记下 id/书脊色,并换掉浏览器默认的半透明快照——
 *  自绘一张「抽出来的书」小卡跟手;App.vue 篓口松手时消费 */
function onDragStart(event: DragEvent, note: NoteListItem): void {
  const data = event.dataTransfer
  if (data === null) return
  data.effectAllowed = 'move'
  data.setData(
    'application/x-baleen-note',
    JSON.stringify({ id: note.id, spine: spineColor(note.id) }),
  )
  // 自定义拖影:竖着的小书(左缘书脊色+衬线题签),像把书从架上抽出来拎着走
  const ghost = document.createElement('div')
  ghost.className = 'drag-book-ghost'
  ghost.style.setProperty('--spine', spineColor(note.id))
  ghost.textContent = note.title
  document.body.appendChild(ghost)
  data.setDragImage(ghost, 24, 33)
  draggingId.value = note.id
  // 拖影在 dragstart 里被同步捕获完,随即摘掉;松手/取消后原位影由 onDragEnd 撤掉
  window.setTimeout(() => ghost.remove(), 0)
}

function onDragEnd(): void {
  draggingId.value = null
}

/** 「删除所选」:纸片逐张飞向右下角纸篓,落篓才入账。
 *  移入可逆(纸篓里可放回),所以不再弹确认——桌面回收站的规矩 */
async function trashSelected(): Promise<void> {
  const ids = Array.from(selectedIds.value)
  if (ids.length === 0 || batchDeleting.value) return
  batchDeleting.value = true
  errorText.value = ''
  try {
    ids.forEach((id, i) => {
      // 逐张错开 70ms 起飞,像一叠纸挨张丢进篓
      window.setTimeout(() => {
        trash.startFly(id)
        void flyToBasket(
          document.querySelector<HTMLElement>(`[data-note-id="${id}"]`),
          spineColor(id),
        )
      }, i * 70)
    })
    // 等最后一张飞到位(错峰延迟 + 飞行时长),再真正调后端
    await new Promise((resolve) => setTimeout(resolve, ids.length * 70 + 620))

    // 逐篇移入纸篓(软删除)。用 allSettled:一篇失败不该中断其余,
    // 但要把失败如实报出来 —— 否则卡片飞走了、后端没记账,刷新又全回来。
    const results = await Promise.allSettled(ids.map((id) => deleteNote(id)))
    const failed = ids.filter((_id, i) => results[i]?.status === 'rejected')
    if (failed.length > 0) {
      errorText.value = `有 ${failed.length} 篇没能移入纸篓,请重试(其余 ${ids.length - failed.length} 篇已移入)`
    }
    // 入库成功后重新拉列表:服务端已排除 archived,不必手动过滤
    await load().catch(() => undefined)
    await trash.refreshCount()
  } finally {
    for (const id of ids) trash.endFly(id)
    selectedIds.value.clear()
    batchDeleting.value = false
  }
}

/** 题签溢出检测:实测超宽才允许 hover 滚动,滚动位移/时长按文字量定 */
function checkTitleOverflow(el: HTMLElement): void {
  const overflowPx = el.scrollWidth - el.clientWidth
  const tracked = el.classList.contains('book-title--overflow')
  if (overflowPx > 2) {
    if (!tracked) el.classList.add('book-title--overflow')
    // 位移 = 溢出像素(滚到文字末尾贴右缘即停,不滚出空白);约 60px/s,最少 3s
    el.style.setProperty('--title-shift', `${-overflowPx}px`)
    el.style.setProperty('--title-scroll-dur', `${Math.max(3, Math.round(el.scrollWidth / 60))}s`)
  } else if (tracked) {
    el.classList.remove('book-title--overflow')
  }
}

/** 本地指令:v-for 里逐个题签量宽,比按字数估计准(短标题不乱滚) */
const vTitleScroll: Directive<HTMLElement> = {
  mounted: checkTitleOverflow,
  updated: checkTitleOverflow,
}

/** ---- 导入笔记(从对话框搬来:文件 → POST /api/sources → 轮询 → 入库) ---- */

interface ImportItem {
  name: string
  /** 推进文案:排队中/解析中/切块向量化中/收尾中/✅ 已入库/↩️ 重复/❌ 失败 */
  stage: string
  status: 'pending' | 'done' | 'deduped' | 'failed'
}

const importFileInput = ref<HTMLInputElement | null>(null)
const importing = ref(false)
const imports = ref<ImportItem[]>([])

const IMPORT_POLL_MS = 2000
const IMPORT_MAX_POLLS = 150 // 2s × 150 = 5 分钟上限
const IMAGE_FILE_RE = /\.(jpe?g|png|gif|webp|bmp|tiff?)$/i

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

function importStageText(detail: SourceDetail): string {
  if (detail.job.status === 'queued') return '排队中'
  if (detail.job.status === 'running') {
    return detail.parse_status === 'ok' ? '切块/向量化中' : '解析中'
  }
  return '收尾中'
}

async function waitImportDone(
  sourceId: number,
  onPoll: (detail: SourceDetail) => void,
): Promise<SourceDetail> {
  for (let i = 0; i < IMPORT_MAX_POLLS; i += 1) {
    const detail = await getSource(sourceId)
    if (detail.job.status === 'done' || detail.job.status === 'failed') return detail
    onPoll(detail)
    await sleep(IMPORT_POLL_MS)
  }
  return getSource(sourceId)
}

/** 单文件导入:更新 imports 里对应条目的推进文案;完成/去重/失败如实标记。 */
async function importOneFile(file: File, attachments: File[] = []): Promise<boolean> {
  if (file.size > MAX_UPLOAD_BYTES) {
    imports.value.push({ name: file.name, stage: '❌ 超过 50MB 上限', status: 'failed' })
    return false
  }
  // reactive 包裹：后续 item.stage = ... 的变更才能触发界面重渲染
  // （裸对象塞进 ref 数组后改原始引用，会绕过响应式触发器——进度会假死）
  const item = reactive<ImportItem>({
    name: file.name,
    stage: attachments.length > 0 ? `排队中(含 ${attachments.length} 张图片)` : '排队中',
    status: 'pending',
  })
  imports.value.push(item)
  let upload
  try {
    upload = await createSource(file, attachments)
  } catch (error) {
    item.stage = `❌ 导入失败:${describeApiError(error)}`
    item.status = 'failed'
    return false
  }
  if (upload.deduped) {
    item.stage = '↩️ 之前导入过,直接复用已有记录'
    item.status = 'deduped'
    return false
  }
  try {
    const detail = await waitImportDone(upload.source_id, (d) => {
      item.stage = importStageText(d)
    })
    if (detail.job.status === 'failed') {
      item.stage = `❌ 导入失败:${detail.job.last_error ?? '处理失败'}`
      item.status = 'failed'
      return false
    }
    if (detail.note_id !== null) {
      item.stage = `✅ 已入库,生成笔记 #${detail.note_id}`
      item.status = 'done'
      return true
    }
    item.stage = `✅ 处理完成(状态:${detail.parse_status},没有生成笔记)`
    item.status = 'done'
    return false
  } catch {
    item.stage = '⚠️ 已提交入库,等结果时连接断了;稍后刷新查看'
    item.status = 'failed'
    return false
  }
}

/**
 * 多文件导入:文档逐个导入,同批的图片作为附件跟随每一个文档(md 相对引用语义)。
 * 图片不单独导入成笔记(那是「AI 看图」路径,不该被批量导入误触发)。
 */
async function importFiles(files: FileList | File[]): Promise<void> {
  if (importing.value) return
  const list = Array.from(files)
  if (list.length === 0) return
  importing.value = true
  imports.value = [] // 新一批开始,清掉旧反馈
  errorText.value = ''
  let produced = false
  try {
    const images = list.filter((f) => IMAGE_FILE_RE.test(f.name))
    const docs = list.filter((f) => !IMAGE_FILE_RE.test(f.name))
    if (docs.length === 0) {
      // 只选了图片:逐张按「导入图片笔记」处理
      for (const f of images) {
        if (await importOneFile(f)) produced = true
      }
    } else {
      for (const f of docs) {
        if (await importOneFile(f, images)) produced = true
      }
    }
  } finally {
    importing.value = false
    // 有新笔记入库 → 刷新书架(解掉「导入后要 F5 才看得见」)
    if (produced) await load()
  }
}

function onImportPicked(event: Event): void {
  const inputEl = event.target as HTMLInputElement
  if (inputEl.files !== null && inputEl.files.length > 0) {
    void importFiles(inputEl.files)
  }
  inputEl.value = '' // 允许下次选同一个文件
}

/** 拖文件到书架区 → 导入。 */
function onShelfDrop(event: DragEvent): void {
  const files = event.dataTransfer?.files
  if (files !== undefined && files.length > 0) {
    event.preventDefault()
    void importFiles(files)
  }
}
</script>

<template>
  <section class="page library-page">
    <div class="search-bar">
      <input
        v-model="keyword"
        class="input"
        type="search"
        placeholder="按标题搜索…"
        aria-label="按标题搜索笔记"
      />
      <span class="muted count">
        {{
          loading
            ? '查询中…'
            : trash.count === 0
              ? `${rangeStart}–${rangeEnd} / ${visibleTotal}`
              : `${rangeStart}–${rangeEnd} / ${visibleTotal} · 纸篓 ${trash.count}`
        }}
      </span>
      <!-- 视图切换:书卡管氛围,列表管查找 -->
      <div class="view-switch" role="group" aria-label="视图切换">
        <button
          class="view-btn"
          :class="{ 'view-btn--active': view === 'books' }"
          type="button"
          title="书架视图"
          aria-label="书架视图"
          @click="setView('books')"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            aria-hidden="true"
          >
            <rect x="3" y="3" width="7" height="7" rx="1.5" />
            <rect x="14" y="3" width="7" height="7" rx="1.5" />
            <rect x="3" y="14" width="7" height="7" rx="1.5" />
            <rect x="14" y="14" width="7" height="7" rx="1.5" />
          </svg>
        </button>
        <button
          class="view-btn"
          :class="{ 'view-btn--active': view === 'list' }"
          type="button"
          title="列表视图"
          aria-label="列表视图"
          @click="setView('list')"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            aria-hidden="true"
          >
            <line x1="4" y1="6" x2="20" y2="6" />
            <line x1="4" y1="12" x2="20" y2="12" />
            <line x1="4" y1="18" x2="20" y2="18" />
          </svg>
        </button>
      </div>
      <button
        class="btn batch-btn"
        :class="{ 'batch-btn--on': selecting }"
        type="button"
        @click="toggleBatch"
      >
        {{ selecting ? '退出批量' : '批量删除' }}
      </button>
      <button
        class="btn btn--primary import-btn"
        type="button"
        :disabled="importing"
        @click="importFileInput?.click()"
      >
        {{ importing ? '导入中…' : '＋ 导入笔记' }}
      </button>
      <!-- 导入文件选择器(隐藏,由按钮/拖放触发;10 种格式 + 图片) -->
      <input
        ref="importFileInput"
        class="visually-hidden"
        type="file"
        multiple
        accept=".pdf,.docx,.md,.markdown,.html,.htm,.txt,.pptx,.csv,.jpg,.jpeg,.png,.gif,.webp,.bmp,.tif,.tiff"
        aria-label="选择要导入的文件"
        @change="onImportPicked"
      />
    </div>

    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <!-- 批量模式:点书选中/取消,「删除所选」把选中的书飞进右下角纸篓(可放回) -->
    <div v-if="selecting" class="batch-bar">
      <span class="batch-count">已选 {{ selectedIds.size }} 本 · 点击书卡选中</span>
      <button
        class="btn batch-del-btn"
        type="button"
        :disabled="selectedIds.size === 0 || batchDeleting"
        @click="trashSelected"
      >
        {{ batchDeleting ? '移入纸篓…' : `删除所选(${selectedIds.size})` }}
      </button>
      <button class="batch-cancel" type="button" :disabled="batchDeleting" @click="toggleBatch">
        取消
      </button>
    </div>

    <!-- 导入进度:一批里每个文件一条,推进/完成/失败如实显示;完成后书架自动刷新 -->
    <div v-if="imports.length > 0" class="import-panel card">
      <p class="import-title">
        导入进度<span v-if="importing" class="muted"> · 处理中…</span>
      </p>
      <ul class="import-list">
        <li v-for="(it, i) in imports" :key="i" class="import-row">
          <span class="import-name" :title="it.name">{{ it.name }}</span>
          <span class="import-stage" :class="`import-stage--${it.status}`">{{ it.stage }}</span>
        </li>
      </ul>
    </div>

    <!-- 书架视图:列数自适应,5:7 书的比例,横排题签。拖文件到此处导入,拖书到纸篓移除 -->
    <ul
      v-if="visibleNotes.length > 0 && view === 'books'"
      class="shelf"
      :class="{ 'shelf--dim': loading }"
      @dragover.prevent
      @drop.prevent="onShelfDrop"
    >
      <li
        v-for="note in visibleNotes"
        :key="note.id"
        class="book"
        :data-note-id="note.id"
        draggable="true"
        :class="{
          'book--selected': selecting && selectedIds.has(note.id),
          'book--flying': trash.flying.includes(note.id),
          'book--dragging': draggingId === note.id,
        }"
        @dragstart="onDragStart($event, note)"
        @dragend="onDragEnd"
        @click.capture="onCardClick($event, note.id)"
      >
        <!-- 批量模式的选中角标:空心圈=未选,实心勾=已选 -->
        <span v-if="selecting" class="book-check" aria-hidden="true">✓</span>
        <RouterLink class="book-link" :to="`/notes/${note.id}`">
          <span class="book-cover" :style="{ '--spine': spineColor(note.id) }">
            <span class="book-spine" />
            <span v-title-scroll class="book-title" :title="note.title"><span class="book-title-text">{{ note.title }}</span></span>
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
          <span class="muted book-date">{{ formatTime(note.created_at) }}</span>
        </span>
      </li>
    </ul>

    <!-- 列表视图:全名 + 质量/段数/日期一行齐,查找管理为主 -->
    <ul
      v-else-if="visibleNotes.length > 0 && view === 'list'"
      class="shelf-rows"
      :class="{ 'shelf--dim': loading }"
      @dragover.prevent
      @drop.prevent="onShelfDrop"
    >
      <li
        v-for="note in visibleNotes"
        :key="note.id"
        class="book-row"
        :data-note-id="note.id"
        draggable="true"
        :class="{
          'book-row--selected': selecting && selectedIds.has(note.id),
          'book--flying': trash.flying.includes(note.id),
          'book--dragging': draggingId === note.id,
        }"
        @dragstart="onDragStart($event, note)"
        @dragend="onDragEnd"
        @click.capture="onCardClick($event, note.id)"
      >
        <RouterLink
          class="row-link"
          :to="`/notes/${note.id}`"
          :style="{ '--spine': spineColor(note.id) }"
        >
          <span v-if="selecting" class="book-check row-check" aria-hidden="true">✓</span>
          <span class="row-bar" />
          <span class="row-title" :title="note.title">{{ note.title }}</span>
        </RouterLink>
        <span class="row-badges">
          <span class="badge" :class="`badge--${qualityTone(note.quality)}`">
            {{ qualityLabel(note.quality) }}
          </span>
          <span class="muted row-num">{{ note.chunk_count }} 段</span>
          <span class="muted row-date">{{ formatTime(note.created_at) }}</span>
        </span>
      </li>
    </ul>

    <p
      v-else-if="!loading && errorText === ''"
      class="empty empty--droppable"
      @dragover.prevent
      @drop.prevent="onShelfDrop"
    >
      {{ appliedKeyword === ''
        ? trash.count > 0
          ? `书架空了——纸篓里还有 ${trash.count} 本。`
          : '书架还是空的——把文件拖到这里即可导入。'
        : `没有标题匹配「${appliedKeyword}」的笔记。` }}
    </p>

    <!-- 翻页:轻量文字链,数字页码一排按钮的重量感与书架不配 -->
    <nav v-if="pageCount > 1" class="pager" aria-label="分页">
      <button class="pager-btn" type="button" :disabled="page === 1 || loading" @click="goPage(page - 1)">
        上一页
      </button>
      <span class="pager-stat">第 {{ page }} / {{ pageCount }} 页</span>
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

/* 导入按钮:推到搜索栏最右 */
.import-btn {
  margin-left: auto;
  flex-shrink: 0;
}

/* ---------- 导入进度面板 ---------- */

.import-panel {
  margin-bottom: 18px;
  padding: 12px 14px;
}

.import-title {
  margin: 0 0 8px;
  font-size: 13.5px;
  font-weight: 650;
}

.import-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.import-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 3px 0;
  font-size: 13px;
  line-height: 1.5;
}

.import-name {
  flex-shrink: 0;
  max-width: 50%;
  overflow: hidden;
  color: var(--ink);
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-stage {
  min-width: 0;
  overflow: hidden;
  color: var(--ink-3);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-stage--done {
  color: var(--ok);
}

.import-stage--deduped {
  color: var(--ink-3);
}

.import-stage--failed {
  color: var(--err);
}

/* 空书架可拖放:虚线框暗示放置区 */
.empty--droppable {
  border: 1.5px dashed var(--line-strong);
  border-radius: 10px;
  padding: 28px 16px;
}

/* ---------- 批量删除 ---------- */

.batch-btn {
  flex-shrink: 0;
  min-width: 118px; /* 与「＋ 导入笔记」同宽,两个动作钮并排不一大一小 */
}

.batch-btn--on {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-ink);
}

/* 批量操作条:浅青底虚线框,像取书时夹在架间的便签 */
.batch-bar {
  display: flex;
  margin-bottom: 14px;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border: 1px dashed var(--accent);
  border-radius: 10px;
  background: var(--accent-soft);
}

.batch-count {
  color: var(--accent-ink);
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

/* 删除所选:朱砂实底——真删除的分量感 */
.batch-del-btn {
  margin-left: auto;
  border-color: var(--seal);
  background: var(--seal);
  color: #fff;
}

.batch-del-btn:hover:not(:disabled) {
  filter: brightness(1.06);
}

.batch-del-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.batch-cancel {
  padding: 4px 8px;
  border: none;
  background: none;
  color: var(--ink-2);
  font-size: 13px;
  cursor: pointer;
}

.batch-cancel:hover:not(:disabled) {
  color: var(--accent-ink);
}

/* ---------- 书架 ---------- */

.shelf {
  display: grid;
  /* 固定 6 列,配合 PAGE_SIZE=12 → 正好 2 行,一屏放下不滚动
     (主人 2026-11-25:「一页两行即可」)。
     不用 auto-fill:那是「能塞几列塞几列」,1440 舞台会铺成 7 列,
     12 本就变成 1 满行 + 5 本残行。
     6 列下书封约 204×285(锁 5:7 真书比例)。 */
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 18px 16px;
  padding: 0;
  margin: 0;
  list-style: none;
}

/* 笔记本体占满父级(stage-inner--fill 给的高度),把翻页推到页底。
   flex:1 + min-height:0:内容多时整页滚,内容少时撑满,翻页都在底部。 */
.library-page {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}

/* 书架/列表/空态自己撑开剩余空间 —— 这是 margin-top:auto 能生效的前提 */
.library-page > .shelf,
.library-page > .shelf-rows,
.library-page > .empty {
  flex: 1 0 auto;
}

.shelf--dim {
  opacity: 0.55;
  transition: opacity 0.15s ease;
}

.book {
  position: relative; /* 批量选择角标的定位锚点(封面左上角) */
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

/* 书的封面:布面装帧(纵向微渐变)+ 左侧颜料书脊 + 顶部横排题签。
   高度不再写死:锁 5:7 真书比例,宽比高长的"明信片感"从此消失 */
.book-cover {
  position: relative;
  display: block;
  aspect-ratio: 5 / 7;
  border: 1px solid var(--line);
  border-radius: 4px 10px 10px 4px;
  background: linear-gradient(180deg, #fffdf8, #f8f4ea);
  box-shadow: 0 1px 2px rgba(45, 40, 32, 0.06);
  overflow: hidden;
  transition:
    transform 0.16s ease,
    box-shadow 0.16s ease,
    border-color 0.16s ease;
}

/* 书脊:左侧一条颜料色窄条 */
.book-spine {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 6px;
  background: var(--spine);
  opacity: 0.9;
}

/* 抽书:悬停时上浮 + 一丝逆时针轻旋,像从架上抽出一格 */
.book-link:hover .book-cover {
  border-color: var(--line-strong);
  box-shadow: var(--shadow-lift);
  transform: translateY(-3px) rotate(-0.5deg);
}

/* 题签:古籍封面上贴的那条白签。单行横排,右缘 12px 渐隐收尾;
   超宽的悬停时向左滚动看全名(位移/时长由 v-title-scroll 指令实测后写入 CSS 变量) */
.book-title {
  position: absolute;
  top: 10px;
  right: 10px;
  left: 14px;
  overflow: hidden;
  padding: 6px 8px;
  border: 1px solid rgba(33, 31, 28, 0.12);
  border-radius: 3px;
  background: rgba(255, 253, 248, 0.92);
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  line-height: 1.55;
  text-align: center;
  white-space: nowrap;
  mask-image: linear-gradient(90deg, #000 calc(100% - 12px), transparent);
  -webkit-mask-image: linear-gradient(90deg, #000 calc(100% - 12px), transparent);
}

.book-title-text {
  display: inline-block;
  padding-right: 1.5em; /* 末尾留口气:滚到尽头时文字尾不贴边 */
  white-space: nowrap;
}

/* 只有真溢出的题签才滚(指令量过才加类);首尾各停一拍,不会一碰就窜 */
.book-title--overflow:hover .book-title-text {
  animation: title-scroll var(--title-scroll-dur, 6s) linear infinite;
}

@keyframes title-scroll {
  0%,
  12% {
    transform: translateX(0);
  }

  88%,
  100% {
    transform: translateX(var(--title-shift, -30%));
  }
}

.book-foot {
  position: absolute;
  right: 8px;
  bottom: 7px;
  left: 14px;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  color: var(--ink-3);
  font-size: 11px;
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

/* 批量选择角标:空心圈=未选,白勾黛青底=已选(仅批量模式渲染,无需 hover 显隐) */
.book-check {
  position: absolute;
  top: 6px;
  left: 6px;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1.5px solid var(--ink-3);
  border-radius: 50%;
  background: rgba(255, 253, 248, 0.95);
  color: transparent;
  font-size: 14px;
  line-height: 1;
}

/* 选中态:整封蒙一层黛青纱 + 白边实心勾,与未选一眼可辨 */
.book--selected .book-cover {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft), var(--shadow);
}

.book--selected .book-cover::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 1;
  background: rgba(61, 90, 115, 0.22);
}

.book--selected .book-check {
  border: 2px solid #fff;
  background: var(--accent);
  color: #fff;
  box-shadow: 0 1px 4px rgba(33, 31, 28, 0.25);
}

/* 飞往纸篓途中:卡片原地淡出缩小,呼应飞出去的那张纸 */
.book--flying {
  opacity: 0;
  transform: scale(0.85);
  transition:
    opacity 0.4s ease,
    transform 0.4s ease;
}

/* 拖起:书被拎离书架,原位只留一道浅影 */
.book--dragging {
  opacity: 0.35;
  transform: scale(0.94) rotate(-2deg);
  transition:
    opacity 0.15s ease,
    transform 0.15s ease;
}

/* 行内角标:随 flex 排在色条前,不绝对定位 */
.book-check.row-check {
  position: static;
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  font-size: 11px;
}

/* ---------- 列表视图:全名可查,查找管理为主 ---------- */

.shelf-rows {
  display: flex;
  margin: 0;
  padding: 0;
  list-style: none;
  flex-direction: column;
  gap: 8px;
}

.book-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 10px 6px 0;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel);
  transition:
    border-color 0.13s ease,
    box-shadow 0.13s ease;
}

.book-row:hover {
  border-color: var(--line-strong);
  box-shadow: var(--shadow);
}

.row-link {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 12px;
  color: inherit;
}

.row-link:hover {
  text-decoration: none;
}

/* 书脊色条延续书架隐喻:一眼认出这是同一本书 */
.row-bar {
  width: 4px;
  align-self: stretch;
  border-radius: 2px;
  background: var(--spine);
  opacity: 0.9;
}

.row-title {
  display: -webkit-box;
  overflow: hidden;
  min-width: 0;
  font-size: 13.5px;
  font-weight: 550;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}

.row-badges {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 10px;
}

.row-num,
.row-date {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 选中行:黛青描边 + 浅青底,与书卡选中态同语言 */
.book-row--selected,
.book-row--selected:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
  box-shadow: 0 0 0 1.5px var(--accent);
}

.book-row--selected .row-check {
  border: 1.5px solid var(--accent);
  background: var(--accent);
  color: #fff;
}

/* ---------- 视图切换钮 ---------- */

.view-switch {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
}

.view-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  color: var(--ink-3);
  cursor: pointer;
  transition:
    background 0.13s ease,
    border-color 0.13s ease,
    color 0.13s ease;
}

.view-btn:hover {
  border-color: var(--accent);
  color: var(--accent-ink);
}

.view-btn--active,
.view-btn--active:hover {
  border-color: var(--ink);
  background: var(--ink);
  color: var(--panel);
}

/* ---------- 翻页:文字链轻量风 ---------- */

/* 翻页钉在页面底部:内容少时(最后一页可能只有 1 本书)不许跟着上移。
   靠 .library-page 的纵向 flex + 本行 margin-top:auto 实现 ——
   不用 position:fixed(会脱离文档流,和侧栏/顶栏滚动打架) */
.pager {
  display: flex;
  margin-top: auto;
  padding-top: 26px;
  align-items: center;
  justify-content: center;
  gap: 14px;
}

.pager-btn {
  padding: 4px 8px;
  border: none;
  background: none;
  color: var(--ink-2);
  font-size: 13px;
  cursor: pointer;
  transition: color 0.13s ease;
}

.pager-btn:hover:not(:disabled) {
  color: var(--accent-ink);
}

.pager-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.pager-stat {
  color: var(--ink-3);
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}
</style>
