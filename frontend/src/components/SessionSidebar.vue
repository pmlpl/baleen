<script setup lang="ts">
/**
 * 问答页侧栏:会话列表 + 搜索 + 右键菜单 + 行内重命名(2026-11-26 从 QaView 拆出)。
 *
 * **为什么拆**:QaView 拆完 NotePicker/SlashMenu 后又长回 2482 行(script 1192 行),
 * 侧栏这一块独占 script 284 行 + template 66 行 + CSS 227 行 ≈ 511 行。
 * 它是**自包含**的:会话列表的搜索/右键/重命名全部只操作列表自身,
 * 与聊天主流程的唯一交集是「点开了哪个会话」,天然适合抽成组件。
 *
 * **契约(与 C2 的 NotePicker/SlashMenu 同一纪律)**:props 进 / emit 出,零 API 封装外的
 * 副作用、零 store。三个数据来源都由父组件持有:
 * - ``sessions`` 父组件拉(``refreshSessions`` 在 send 的 onDone 里也要调)
 * - ``currentSessionId`` 高亮用,父组件是唯一真源
 * - ``sending`` 回答中禁止改会话元数据(删除/重命名可能让当前流落到不存在的会话)
 *
 * 重命名成功只改**本地那一行**(后端刻意不刷 updated_at,位次不动,无需重拉列表),
 * 通过 ``renamed`` 事件让父组件同步它持有的数组 —— 不直接改 prop。
 */
import type { ComponentPublicInstance, Directive } from 'vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { describeApiError } from '../api/http'
import { deleteSession, renameSession, SESSION_TITLE_MAX, type QaSessionItem } from '../api/qa'
import { getToken } from '../auth/token'
import { confirmDialog } from '../utils/confirm'

defineOptions({ name: 'SessionSidebar' })

const props = defineProps<{
  /** 全部会话(父组件持有;本组件只做过滤,不改原数组) */
  sessions: QaSessionItem[]
  /** 当前打开的会话(高亮用);null = 新对话 */
  currentSessionId: number | null
  /** 回答进行中:禁止改会话元数据 */
  sending: boolean
}>()

const emit = defineEmits<{
  /** 点「+ 新对话」 */
  new: []
  /** 打开某个会话 */
  open: [id: number]
  /** 重命名成功(父组件据此同步它的 sessions 数组) */
  renamed: [id: number, title: string]
  /** 会话已删除(父组件决定是否 startNew + 重拉列表) */
  deleted: [id: number]
  /** 侧栏内发生的用户可见错误(交给父组件统一显示) */
  error: [message: string]
}>()

// ---------------- 搜索 ----------------

const sessionQuery = ref('')

/** 按标题过滤:会话多了能定位,不引入额外复杂度 */
const filteredSessions = computed<QaSessionItem[]>(() => {
  const q = sessionQuery.value.trim().toLowerCase()
  if (q === '') return props.sessions
  return props.sessions.filter((s) => s.title.toLowerCase().includes(q))
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

// ---------------- 右键菜单 ----------------

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
  if (props.sending) return
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

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointerDown, true)
  document.removeEventListener('keydown', onDocKeydown)
})

// ---------------- 行内重命名 ----------------

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
  if (props.sending) return
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
  const target = props.sessions.find((s) => s.id === state.sessionId)
  if (target !== undefined && target.title === title) {
    cancelRename() // 没改就不发请求
    return
  }
  try {
    const updated = await renameSession(state.sessionId, title)
    emit('renamed', updated.id, updated.title)
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

// ---------------- 导出 / 删除 ----------------

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
      emit('error', resp.status === 401 ? '登录已过期,请重新登录' : `导出失败(${resp.status})`)
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
    emit('error', '导出失败:连不上后端')
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
    emit('deleted', id)
  } catch (error) {
    emit('error', describeApiError(error))
  }
}

// ---- 菜单三项:菜单只记 sessionId,按 id 找行再分发(菜单自己不持有整个会话对象) ----

function startRenameById(sessionId: number): void {
  const target = props.sessions.find((s) => s.id === sessionId)
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

// ---------------- 标题悬停滚动 ----------------

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
</script>

<template>
  <aside class="session-side">
    <div class="session-side-inner">
      <button class="btn btn--primary new-btn" type="button" @click="emit('new')">+ 新对话</button>
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
            @click="emit('open', s.id)"
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

  <!-- 右键菜单:挂 body 不受侧栏滚动容器裁剪 -->
  <Teleport to="body">
    <div
      v-if="rowMenu.open"
      ref="rowMenuEl"
      class="row-menu card"
      :style="{ left: `${rowMenu.x}px`, top: `${rowMenu.y}px` }"
      @contextmenu.prevent
    >
      <button class="row-menu-item" type="button" role="menuitem" @click="startRenameById(rowMenu.sessionId)">
        <span class="row-menu-ico" aria-hidden="true">✎</span>
        <span>重命名</span>
      </button>
      <button
        class="row-menu-item"
        type="button"
        role="menuitem"
        @click="exportById(rowMenu.sessionId)"
      >
        <span class="row-menu-ico" aria-hidden="true">↓</span>
        <span>导出 Markdown</span>
      </button>
      <div class="row-menu-sep" aria-hidden="true"></div>
      <button
        class="row-menu-item row-menu-item--danger"
        type="button"
        role="menuitem"
        @click="deleteById(rowMenu.sessionId)"
      >
        <span class="row-menu-ico" aria-hidden="true">×</span>
        <span>删除</span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
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

.session-date {
  flex-shrink: 0;
  color: var(--ink-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.session-open:hover {
  color: var(--accent-ink);
}

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

.session-rename-input--over {
  border-color: var(--seal);
  box-shadow: 0 0 0 3px rgba(176, 58, 46, 0.14);
}

.session-rename-error {
  margin-top: 6px;
  font-size: 12px;
}

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

.session-search {
  margin-bottom: 10px;
  padding: 6px 10px;
  font-size: 13px;
}
</style>
