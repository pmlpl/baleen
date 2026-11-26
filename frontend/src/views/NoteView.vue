<script setup lang="ts">
/**
 * 笔记详情:整篇连续阅读 + 左侧锚点目录。
 *
 * 数据是 GET /api/notes/{id} 的 parents(父块真源)。所有父块按 ord 顺序**连续渲染**
 * 成一整篇,左侧目录只是锚点 —— 点目录滚动到对应位置,不是切换内容。
 *
 * 滚动时用 IntersectionObserver 反查「现在看的是哪一节」,左侧目录同步高亮,
 * 点过的锚点也能通过 URL 的 #hash 记住(刷新/分享能回到同一节)。
 *
 * markdown 有正文时不在这里渲染(父块才是真源);渲染器手写,原文先转义,
 * 笔记里写了 <script> 也只显示成文字。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import { deleteNote, getNote, updateNote } from '../api/notes'
import type { NoteDetail, NoteParent } from '../api/notes'
import { prevRoute } from '../router/prevRoute'
import { applyFigureLayout } from '../utils/figure-layout'
import { renderMermaidIn } from '../utils/mermaid'
import { renderMindmapsIn } from '../utils/mindmap'
import { confirmDialog } from '../utils/confirm'
import { renderSectionText, collectCitationTargets } from '../utils/section'
import { renderSvgsIn } from '../utils/svg'

const props = defineProps<{ id: string }>()

const router = useRouter()

/**
 * 返回按钮的来源感知:从问答进来显示「返回对话」,从书架进来显示「返回书架」。
 * 有上一条应用内路由就 router.back()(回到离开时的现场,问答会话原样还在);
 * 直接刷新/外链打开(prevRoute 为 null)则兜底回书架。
 */
const backLabel = computed<string>(() => {
  switch (prevRoute.value?.name) {
    case 'qa':
      return '返回对话'
    case 'notes':
      return '返回书架'
    case 'note':
      return '返回笔记'
    case 'video':
      return '返回视频'
    case 'settings':
      return '返回设置'
    case 'charter':
      return '返回章程'
    default:
      return ''
  }
})

function goBack(): void {
  router.back()
}

const note = ref<NoteDetail | null>(null)
const loading = ref(false)
const errorText = ref('')

/** ---- 编辑模式(用户自编辑) ---- */
const editing = ref(false)
const editTitle = ref('')
const editMarkdown = ref('')
/** 进入编辑时的快照:只提交真正改过的字段(含图笔记只改标题才不会撞 400) */
const editSnapshot = ref<{ title: string; markdown: string }>({ title: '', markdown: '' })
const saving = ref(false)
const saveError = ref('')
const savedHint = ref('')

/** 当前视口内的章节 id,用于目录高亮 */
const activeId = ref<number | null>(null)
/** 目录列表元素(高亮项自动滚进可视区用) */
const tocListEl = ref<HTMLElement | null>(null)
/** 左栏滚动容器(目录本身过高时在这里滚) */
const tocEl = ref<HTMLElement | null>(null)
/** 右栏滚动容器(正文在这里滚,不是整页滚) */
const contentEl = ref<HTMLElement | null>(null)

/** 图片灯箱(2026-11-19 复核批 P2):正文插图限宽后,点图放大看细节 */
const lightboxSrc = ref<string | null>(null)

function onContentClick(event: MouseEvent): void {
  const img = (event.target as HTMLElement).closest<HTMLImageElement>('.note-img img')
  if (img !== null && img.src !== '') {
    event.preventDefault()
    lightboxSrc.value = img.src
  }
}

function closeLightbox(): void {
  lightboxSrc.value = null
}

/** 目录条目:章节名 + 缩进层级 + 原块 */
interface TocEntry {
  parent: NoteParent
  /** 列表 key:父块 id + 深度(同一父块会展开出多条,裸 chunk_id 会撞 key) */
  key: string
  label: string
  depth: number
  /** 是该父块路径的最后一段(高亮只点叶子,免得「二」和「2.1」同时亮) */
  isLeaf: boolean
}

/**
 * heading_path → 目录条目(逐段展开)。
 * 「二、镜像 > 2.1 docker images」现在产出**两条**:二(0 级)+ 2.1(1 级)——
 * 旧版每个父块只出最后一段,而「纯容器型 H1」(标题下无正文直接接 H2)没有
 * 自己的父块,只作为 2.x 父块的路径前缀存在,目录里就永远看不到「二、三、五」
 * (2026-11-25 note 9353 实锤:目录只有一和四)。展开后按路径前缀去重,
 * 已有独立父块的层级(如一/四)不会重复出现。
 */
const toc = computed<TocEntry[]>(() => {
  if (note.value === null) return []
  const title = note.value.title
  const entries: TocEntry[] = []
  const seen = new Set<string>()
  for (const parent of note.value.parents) {
    const raw = (parent.heading_path ?? '').trim()
    let parts: string[] = []
    if (raw !== '') {
      parts = raw
        .split('>')
        .map((p) => p.trim())
        .filter((p) => p !== '')
      // 去掉开头与笔记标题相同的那一段(它只是根);标题可能被截断,容忍前缀关系
      if (parts.length > 1) {
        const head = parts[0] as string
        if (head === title || title.startsWith(head) || head.startsWith(title)) {
          parts.shift()
        }
      }
    }
    if (parts.length === 0) {
      const key = `p${parent.chunk_id}`
      if (!seen.has(key)) {
        seen.add(key)
        entries.push({ parent, key, label: '正文', depth: 0, isLeaf: true })
      }
      continue
    }
    parts.forEach((seg, i) => {
      const key = `p${parent.chunk_id}-${i}`
      const pathKey = parts.slice(0, i + 1).join(' > ')
      if (!seen.has(pathKey)) {
        seen.add(pathKey)
        entries.push({ parent, key, label: seg, depth: i, isLeaf: i === parts.length - 1 })
      }
    })
  }
  return entries
})

/** 每个父块 id → 它的排版后 HTML(按父块顺序消费图片清单,v16) */
const htmlById = computed<Map<number, string>>(() => {
  const map = new Map<number, string>()
  if (note.value === null) return map
  // 出处编号 → 地址 的表要从**整篇**扫:正文的 [W1] 和文末清单往往不在同一个父块里
  const targets = collectCitationTargets(note.value.parents.map((p) => p.text))
  // 游标容器:父块按 ord 顺序渲染,图片清单也是文档顺序,顺序消费即可对上原位
  const cursor = { i: 0 }
  for (const parent of note.value.parents) {
    map.set(parent.chunk_id, renderSectionText(parent.text, note.value.images, cursor, targets))
  }
  return map
})

/** 平滑滚动期间锁住高亮(否则中途经过的节会闪一下又跳回来) */
let lockUntil = 0

/** 点目录:让右栏滚到对应块(不换内容),并更新 URL hash */
function scrollToSection(chunkId: number, smooth = true): void {
  const container = contentEl.value
  const target = document.getElementById(`sec-${chunkId}`)
  if (container === null || target === null) return
  activeId.value = chunkId
  syncTocScroll(chunkId)
  lockUntil = Date.now() + 600 // 平滑滚动大致耗时,期间不做位置反查
  // 右栏是独立滚动容器,用相对它的偏移量精确定位(留 8px 呼吸空间)
  const top =
    target.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 8
  container.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' })
  // hash 只用于记忆位置,不触发路由重载
  history.replaceState(null, '', `#sec-${chunkId}`)
}

/**
 * 滚动时反查「当前在看哪一节」。
 *
 * 用滚动位置直接算,不用 IntersectionObserver:后者的回调只带「状态变化的块」,
 * 多块同时可见时会漏判,滚轮快速滑动时高亮容易停在上一个。
 *
 * 规则:取「顶部已经越过判定线(视口上沿往下 120px)」的最后一节;
 * 一节都还没到就取第一节。这样滚到哪节,左边就跟到哪节。
 */
const ACTIVE_OFFSET_PX = 60

function computeActive(): void {
  if (Date.now() < lockUntil) return // 程序化滚动中,别和用户意图打架
  if (toc.value.length === 0) return
  const container = contentEl.value
  if (container === null) return
  // 基准是右栏容器的上沿(不是浏览器视口):右栏自己滚,容器位置不变
  const base = container.getBoundingClientRect().top
  let current = toc.value[0]?.parent.chunk_id ?? null
  for (const entry of toc.value) {
    const el = document.getElementById(`sec-${entry.parent.chunk_id}`)
    if (el === null) continue
    if (el.getBoundingClientRect().top - base <= ACTIVE_OFFSET_PX) {
      current = entry.parent.chunk_id
    } else {
      break // 后面的节都在下方,不用再看
    }
  }
  if (current !== activeId.value) {
    activeId.value = current
    syncTocScroll(current)
  }
}

/** 让目录里高亮的那一项保持在可视区域内(目录超高时自己滚) */
function syncTocScroll(chunkId: number | null): void {
  if (chunkId === null) return
  const list = tocListEl.value
  const box = tocEl.value
  if (list === null || box === null) return
  const active = list.querySelector<HTMLElement>(`[data-sec="${chunkId}"]`)
  if (active === null) return
  const listRect = box.getBoundingClientRect()
  const itemRect = active.getBoundingClientRect()
  const margin = 24
  if (itemRect.top < listRect.top + margin) {
    box.scrollTop -= listRect.top + margin - itemRect.top
  } else if (itemRect.bottom > listRect.bottom - margin) {
    box.scrollTop += itemRect.bottom - (listRect.bottom - margin)
  }
}

/** 滚动监听:rAF 节流,一帧只算一次 */
let rafPending = false

function onScroll(): void {
  if (rafPending) return
  rafPending = true
  requestAnimationFrame(() => {
    rafPending = false
    computeActive()
  })
}

/** 绑定 / 解绑(右栏容器滚动 + 尺寸变化) */
function bindScrollTracking(): void {
  contentEl.value?.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll)
}

function unbindScrollTracking(): void {
  contentEl.value?.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
}

/** 首次进入:若带 hash 就滚过去,否则停在顶部 */
function restoreScroll(): void {
  const hash = window.location.hash
  if (hash.startsWith('#sec-')) {
    const id = Number(hash.slice(5))
    if (Number.isFinite(id) && document.getElementById(hash) !== null) {
      activeId.value = id
      void nextTick(() => scrollToSection(id, false))
      return
    }
  }
  const first = toc.value.find((e) => e.parent.text.trim().length > 0)
  activeId.value = first ? first.parent.chunk_id : null
  contentEl.value?.scrollTo({ top: 0 })
}

async function load(): Promise<void> {
  const noteId = Number(props.id)
  if (!Number.isFinite(noteId)) {
    note.value = null
    errorText.value = `笔记编号「${props.id}」不合法`
    return
  }
  loading.value = true
  errorText.value = ''
  savedHint.value = ''
  unbindScrollTracking() // 换笔记时先摘掉旧监听,避免叠加
  try {
    note.value = await getNote(noteId)
  } catch (error) {
    note.value = null
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
  if (note.value === null) return
  // 等 v-if 把正文渲染出来再取容器:contentEl/tocEl 到这一步才存在
  await nextTick()
  // mermaid 代码块渲染成图(懒加载;失败退化为代码块+角标,绝不抛错)
  // SVG 插图同理:沙箱 iframe 展示,失败退化代码块
  if (contentEl.value !== null) {
    void renderMermaidIn(contentEl.value)
    void renderMindmapsIn(contentEl.value)
    void renderSvgsIn(contentEl.value)
    // 插图按宽高比分类(窄图左浮动文字环绕,宽图保持居中限宽;见 figure-layout.ts)
    applyFigureLayout(contentEl.value)
  }
  restoreScroll()
  computeActive()
  bindScrollTracking()
}

watch(
  () => props.id,
  () => void load(),
  { immediate: true },
)

onBeforeUnmount(() => {
  unbindScrollTracking()
})

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

/** ---- 编辑模式 ---- */

function startEdit(): void {
  if (note.value === null) return
  editSnapshot.value = { title: note.value.title, markdown: note.value.markdown }
  editTitle.value = note.value.title
  editMarkdown.value = note.value.markdown
  saveError.value = ''
  savedHint.value = ''
  editing.value = true
}

function cancelEdit(): void {
  editing.value = false
  saveError.value = ''
}

async function saveEdit(): Promise<void> {
  if (note.value === null || saving.value) return
  const snap = editSnapshot.value
  const body: { title?: string; markdown?: string } = {}
  if (editTitle.value !== snap.title) body.title = editTitle.value
  if (editMarkdown.value !== snap.markdown) body.markdown = editMarkdown.value
  if (body.title === undefined && body.markdown === undefined) {
    savedHint.value = '没有改动。'
    editing.value = false
    return
  }
  saving.value = true
  saveError.value = ''
  try {
    const result = await updateNote(note.value.id, body)
    editing.value = false
    await load() // 目录/正文按新 doc 重新渲染(load 会清 savedHint,提示要在其后赋值)
    savedHint.value = result.content_changed
      ? `已保存,检索索引已重建(父块 ${result.chunks_parents} / 子块 ${result.chunks_children})`
      : '已保存。'
  } catch (error) {
    saveError.value = describeApiError(error)
  } finally {
    saving.value = false
  }
}

/** ---- 删除(真删除:正文/切块/向量/原文件一起清,所以必须二次确认) ---- */
const deleting = ref(false)

async function removeNote(): Promise<void> {
  if (note.value === null || deleting.value) return
  const ok = await confirmDialog({
    title: `删除《${note.value.title}》`,
    body: '正文、检索索引和原文件会一起删除,不可恢复。',
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  deleting.value = true
  try {
    await deleteNote(note.value.id)
    await router.push({ name: 'notes' })
  } catch (error) {
    errorText.value = describeApiError(error)
    deleting.value = false
  }
}
</script>

<template>
  <section class="page note-page">
    <a v-if="backLabel !== ''" class="back" role="link" tabindex="0" @click="goBack" @keydown.enter="goBack">
      ← {{ backLabel }}
    </a>
    <RouterLink v-else class="back" :to="{ name: 'notes' }">← 回书架</RouterLink>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <template v-else-if="note !== null">
      <header class="page-head note-head">
        <div class="note-head-row">
          <h1 class="page-title">{{ note.title }}</h1>
          <div class="note-actions">
            <button class="btn" type="button" :disabled="editing" @click="startEdit">编辑</button>
            <button
              class="btn btn--danger"
              type="button"
              :disabled="editing || deleting"
              @click="removeNote"
            >
              {{ deleting ? '删除中…' : '删除' }}
            </button>
          </div>
        </div>
        <p v-if="savedHint !== ''" class="note note--ok saved-hint">{{ savedHint }}</p>
        <div class="detail-meta">
          <span>{{ qualityLabel(note.quality) }}</span>
          <span v-if="note.lang !== ''">{{ note.lang }}</span>
          <span v-if="note.tags.length > 0">{{ note.tags.join(' / ') }}</span>
          <span v-if="note.source_id !== null">来源 #{{ note.source_id }}</span>
          <span>{{ toc.length }} 节</span>
        </div>
      </header>

      <!-- 编辑模式:标题 + markdown 源码;保存后后端重建切块与向量 -->
      <div v-if="editing" class="editor">
        <label class="editor-field">
          <span class="editor-label">标题</span>
          <input v-model="editTitle" class="input" type="text" maxlength="200" />
        </label>
        <label class="editor-field">
          <span class="editor-label">正文(markdown 源码)</span>
          <textarea
            v-model="editMarkdown"
            class="editor-md"
            spellcheck="false"
            placeholder="# 标题&#10;&#10;正文…"
          ></textarea>
        </label>
        <p class="muted editor-hint">
          图片以 <code>![说明](baleen-image:N)</code> 标记出现:挪动标记行图片就跟着挪,删掉标记行图片也会删除。
        </p>
        <p v-if="saveError !== ''" class="note note--error">{{ saveError }}</p>
        <div class="editor-actions">
          <button class="btn btn--primary" type="button" :disabled="saving" @click="saveEdit">
            {{ saving ? '保存中…' : '保存' }}
          </button>
          <button class="btn btn--ghost" type="button" :disabled="saving" @click="cancelEdit">
            取消
          </button>
        </div>
      </div>

      <div v-else class="reader">
        <!-- 左栏:锚点目录(sticky,独立滚动) -->
        <aside ref="tocEl" class="toc" aria-label="章节目录">
          <div class="toc-inner">
            <p class="toc-title">目录</p>
            <ul v-if="toc.length > 0" ref="tocListEl" class="toc-list">
              <li v-for="entry in toc" :key="entry.key">
                <button
                  class="toc-item"
                  :class="{ 'toc-item--active': entry.isLeaf && entry.parent.chunk_id === activeId }"
                  :style="{ paddingLeft: `${10 + entry.depth * 14}px` }"
                  type="button"
                  :data-sec="entry.parent.chunk_id"
                  :title="entry.parent.heading_path"
                  @click="scrollToSection(entry.parent.chunk_id)"
                >
                  {{ entry.label }}
                </button>
              </li>
            </ul>
            <p v-else class="muted toc-empty">这篇没有分节记录。</p>
          </div>
        </aside>

        <!-- 右栏:整篇连续正文,每节一个锚点;插图点击委托到灯箱 -->
        <article ref="contentEl" class="content" @click="onContentClick">
          <section
            v-for="entry in toc"
            :id="`sec-${entry.parent.chunk_id}`"
            :key="entry.parent.chunk_id"
            class="sec-block"
          >
            <h2 v-if="entry.label !== '正文'" class="sec-heading" :data-depth="entry.depth">
              {{ entry.label }}
            </h2>
            <!-- 父块原文经 renderSectionText 分块(段落/代码)并逐行转义,不是原文直插 -->
            <div class="sec" v-html="htmlById.get(entry.parent.chunk_id)" />
          </section>

          <p v-if="toc.length === 0" class="empty">这条笔记没有可显示的内容。</p>
        </article>
      </div>
    </template>

    <!-- 图片灯箱:点图放大,点击任意处关闭 -->
    <div v-if="lightboxSrc !== null" class="img-lightbox" @click="closeLightbox">
      <img :src="lightboxSrc" alt="放大查看" />
    </div>
  </section>
</template>

<style scoped>
.note-page {
  display: flex;
  /* 撑满舞台剩余高度,页面本身不滚(滚动交给下面两栏)。
     高度由 App.vue 的 .stage--clamp 提供(视口 − 顶栏),这里只管占满 ——
     以前在本页写 calc(100vh - 96px),顶栏 53 + 舞台内边距 82 一加就多滚 39px */
  flex: 1;
  min-height: 420px;
  flex-direction: column;
}

.back {
  display: inline-block;
  margin-bottom: 12px;
  font-size: 13.5px;
}

/* 扉页:标题区下一条细墨线,阅读区从线下展开,像翻开一本书 */
.note-head {
  margin-bottom: 20px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.note-head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

/* 扉页操作:阅读时隐身,悬停标题区/键盘聚焦才浮现;触屏无悬停常显 */
.note-actions {
  display: flex;
  gap: 8px;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.note-head:hover .note-actions,
.note-head:focus-within .note-actions {
  opacity: 1;
}

@media (hover: none) {
  .note-actions {
    opacity: 0.9;
  }
}

.saved-hint {
  margin: 8px 0 0;
}

/* ---------- 编辑模式 ---------- */

.editor {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.editor-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.editor-label {
  color: var(--ink-3);
  font-size: 12.5px;
  font-weight: 600;
}

.editor-md {
  flex: 1;
  min-height: 260px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--ink);
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.7;
  resize: none;
}

.editor-md:focus {
  outline: none;
  border-color: var(--accent);
}

.editor-hint {
  margin: 0;
  font-size: 12.5px;
}

.editor-actions {
  display: flex;
  gap: 10px;
}

/* 扉页元信息:一行点隔(分隔点自动加),去掉「质量: 标签:」的表单腔 */
.detail-meta {
  display: flex;
  margin-top: 8px;
  flex-wrap: wrap;
  align-items: center;
  color: var(--ink-3);
  font-size: 12.5px;
  line-height: 1.7;
}

.detail-meta > span + span::before {
  content: '·';
  margin: 0 8px;
}

/* ---------- 两栏 ---------- */

.reader {
  display: grid;
  /* 关键:两栏都占满剩余高度,各自内部滚动 → 底边天然对齐且都没有空白 */
  flex: 1;
  min-height: 0;
  grid-template-columns: 236px minmax(0, 1fr);
  gap: 24px;
  align-items: stretch;
}

/* 左栏:固定一屏高,目录多了自己滚 */
.toc {
  min-width: 0;
  min-height: 0;
  padding: 14px 10px;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}

/* 左栏不再需要 sticky:它自己就是滚动容器 */
.toc-inner {
  display: contents;
}

.toc-title {
  margin: 0 0 8px;
  padding: 0 6px;
  color: var(--ink-3);
  font-family: var(--font-serif);
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.5em;
}

.toc-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.toc-item {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-left: 2px solid transparent;
  border-radius: 6px;
  background: none;
  color: var(--ink-2);
  cursor: pointer;
  font-size: 13px;
  line-height: 1.45;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: background 0.13s ease, color 0.13s ease;
}

.toc-item:hover {
  background: var(--accent-soft);
  color: var(--accent-ink);
}

.toc-item--active {
  border-left-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-weight: 600;
}

.toc-empty {
  padding: 0 6px;
  font-size: 12.5px;
}

/* ---------- 右栏:连续正文 ---------- */

.content {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  /* 给滚动条留点空间,末行不被贴边 */
  padding-right: 6px;
}

.sec-block {
  /* 锚点跳转时给标题留出呼吸空间 */
  scroll-margin-top: 16px;
  padding-bottom: 26px;
  margin-bottom: 26px;
  border-bottom: 1px solid var(--line);
}

.sec-block:last-of-type {
  border-bottom: none;
}

.sec-heading {
  margin: 0 0 14px;
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.02em;
  line-height: 1.4;
}

/* 二级章节稍小一点 */
.sec-heading[data-depth='1'] {
  padding-left: 10px;
  border-left: 3px solid var(--accent-soft);
  font-size: 16px;
}

.sec-heading[data-depth='2'] {
  padding-left: 10px;
  border-left: 3px solid var(--accent-soft);
  color: var(--ink-2);
  font-size: 15px;
}

.sec {
  color: var(--ink);
  font-size: 15px;
  line-height: 1.9;
  word-break: break-word;
}

.sec :deep(p) {
  margin: 0 0 14px;
}

.sec :deep(p:last-child) {
  margin-bottom: 0;
}

.sec :deep(.sec-code) {
  margin: 0 0 16px;
  padding: 13px 16px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--neutral-soft);
  color: var(--ink);
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre;
}

/* 围栏代码块的语言角标(display 序列化 2026-11-19;mermaid 块被渲染成图后此样式自然消失) */
.sec :deep(.sec-code[data-lang])::before {
  content: attr(data-lang);
  display: block;
  margin-bottom: 6px;
  color: var(--ink-3);
  font-size: 11px;
  text-align: right;
}

/* 行内代码:轻底色等宽,长命令可折行 */
.sec :deep(.sec-code-inline) {
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--neutral-soft);
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 0.92em;
}

/* 列表(display 序列化):与段落拉开层级,行距略密 */
.sec :deep(.sec-list) {
  margin: 0 0 14px;
  padding-left: 1.8em;
  line-height: 1.75;
}

.sec :deep(.sec-list li) {
  margin: 0 0 6px;
}

.sec :deep(.sec-list li:last-child) {
  margin-bottom: 0;
}

/* 引用块(display 序列化):左边线 + 次级墨色,视觉上与正文分层 */
.sec :deep(.sec-quote) {
  margin: 0 0 14px;
  padding: 6px 14px;
  border-left: 3px solid var(--accent-soft);
  border-radius: 0 8px 8px 0;
  background: var(--panel);
  color: var(--ink-2);
}

/* 笔记插图:md 导入时随笔记存字节,阅读页按原位渲染 */
.sec :deep(.note-img) {
  margin: 0 0 16px;
  text-align: center;
}

.sec :deep(.note-img img) {
  /* 限宽:1280px 视频帧不再撑满整行,两侧留白(2026-11-19 复核批 P2);细节点开灯箱看 */
  max-width: min(100%, 640px);
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  cursor: zoom-in;
}

/*
 * 方案 C(2026-11-19 主人拍板):按图片原始宽高比自适应。
 * figure-layout.ts 在 img 载入后给 figure 打 is-narrow / is-wide:
 *   窄图(w/h ≤ 4/3:竖图/方图/课件竖版) → 左浮动小图,文字环绕,不浪费整行
 *   宽图(16:9 视频帧/屏幕截图)         → 保持上面那条居中限宽(该大就大)
 * 阈值与 class 名在 figure-layout.ts,这里只管样式。
 * 注意:不要给后续 <p> 加 overflow:hidden —— 那会让每段都变成 BFC,
 * 文字永远挤在窄列里回不到整行。浮动下文字本来就会自然环绕,
 * 只需在父块末尾清浮动(.sec::after),保证下一节不从图上骑过去。
 */
.sec :deep(.note-img.is-narrow) {
  float: left;
  width: 38%;
  max-width: 300px;
  margin: 4px 20px 12px 0;
  text-align: left;
}

.sec :deep(.note-img.is-narrow img) {
  max-width: 100%;
  display: block;
}

.sec :deep(.note-img.is-narrow figcaption) {
  text-align: left;
}

/* 父块末尾清浮动:浮动图不越界到下一节 */
.sec::after {
  content: '';
  display: table;
  clear: both;
}

/* 窄屏(900px 以下,与阅读区两栏→单栏同断点):浮动取消,恢复居中整行 */
@media (max-width: 900px) {
  .sec :deep(.note-img.is-narrow) {
    float: none;
    width: 100%;
    max-width: 100%;
    margin: 0 0 16px;
    text-align: center;
  }

  .sec :deep(.note-img.is-narrow figcaption) {
    text-align: center;
  }
}

/* 图片灯箱:点图放大,点击任意处关闭 */
.img-lightbox {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(15, 15, 15, 0.82);
  cursor: zoom-out;
}

.img-lightbox img {
  max-width: 100%;
  max-height: 100%;
  border-radius: 6px;
}

.sec :deep(.note-img figcaption) {
  margin-top: 6px;
  color: var(--ink-3);
  font-size: 12.5px;
}

.sec :deep(.img-missing) {
  color: var(--ink-3);
  font-size: 13px;
}

/* 窄屏:目录折到上方,整页改为自然高度滚动(父级 .stage--clamp 高度放开见 App.vue) */
@media (max-width: 900px) {
  .note-page {
    flex: none;
  }

  .reader {
    grid-template-columns: minmax(0, 1fr);
  }

  .toc {
    max-height: 240px;
  }

  .content {
    overflow: visible;
  }
}
</style>
