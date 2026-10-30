<script setup lang="ts">
/**
 * 导入页:拖拽/点选 → POST /api/sources → 轮询 GET /api/sources/{id}。
 * 上传与轮询的状态机全在 stores/import.ts,这里只负责渲染和事件绑定。
 */
import { storeToRefs } from 'pinia'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { JobStatus, ParseStatus, ReviewRun, SourceDetail } from '../api/sources'
import type { ImportItem } from '../stores/import'
import { jobIsTerminal, MAX_UPLOAD_BYTES, useImportStore } from '../stores/import'

const store = useImportStore()
const { items, isBusy } = storeToRefs(store)

const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

type Tone = 'ok' | 'warn' | 'error' | 'neutral'

/** job.status 五态的中文文案与配色(docs/api.md §2) */
const JOB_LABELS: Record<JobStatus, string> = {
  queued: '排队中',
  running: '处理中',
  done: '成功',
  failed: '失败',
  cancelled: '已取消',
}

const JOB_TONES: Record<JobStatus, Tone> = {
  queued: 'neutral',
  running: 'warn',
  done: 'ok',
  failed: 'error',
  cancelled: 'neutral',
}

/** 闸门 verdict 的中文(后端目前会产出 pass/accept/partial/reject,未知值原样显示) */
const VERDICT_LABELS: Record<string, string> = {
  pass: '通过',
  accept: '收下',
  partial: '部分收下',
  reject: '拒收',
  drop: '丢弃',
  fail: '未通过',
  skip: '跳过',
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

/** 卡片右上角的状态:还没拿到 source 时按前端阶段显示 */
function itemLabel(item: ImportItem): string {
  if (item.phase === 'pending') return '等待上传'
  if (item.phase === 'uploading') return '上传中'
  if (item.phase === 'upload-failed') return '未入库'
  const status = item.source?.job.status
  return status === undefined ? '已接收' : JOB_LABELS[status]
}

function itemTone(item: ImportItem): Tone {
  if (item.phase === 'pending' || item.phase === 'uploading') return 'neutral'
  if (item.phase === 'upload-failed') return 'error'
  const status = item.source?.job.status
  return status === undefined ? 'neutral' : JOB_TONES[status]
}

/**
 * parse_status 的人话。
 * ⚠️ no_text_expected 是「扫描件/纯图片,本来就没有文本层」,不是错误,用中性色,别画红。
 */
function describeParse(source: SourceDetail): { text: string; tone: Tone } {
  const status: ParseStatus = source.parse_status
  if (status === 'ok') return { text: '解析正常', tone: 'ok' }
  if (status === 'no_text_expected') return { text: '扫描件/纯图片,无文本层', tone: 'neutral' }
  if (status === 'unsupported_format') {
    return { text: source.message ?? '格式不支持(后端没给原因)', tone: 'warn' }
  }
  // 还没轮到 worker 时,后端会把 parse_status 初始化成 error,别误报成「解析失败」
  if (source.job.status === 'queued' || source.job.status === 'running') {
    return { text: '等待解析', tone: 'neutral' }
  }
  return { text: source.message ?? '解析失败(后端没给原因)', tone: 'error' }
}

function verdictLabel(run: ReviewRun): string {
  return VERDICT_LABELS[run.verdict] ?? run.verdict
}

function verdictTone(run: ReviewRun): Tone {
  if (run.verdict === 'pass' || run.verdict === 'accept') return 'ok'
  if (run.verdict === 'partial') return 'warn'
  if (run.verdict === 'reject' || run.verdict === 'drop' || run.verdict === 'fail') return 'error'
  return 'neutral'
}

/** 终态(或前端侧失败)就不必再显示「刷新状态」 */
function isFinished(item: ImportItem): boolean {
  if (item.phase === 'upload-failed') return true
  return jobIsTerminal(item.source?.job.status)
}

function openPicker(): void {
  fileInput.value?.click()
}

function onPicked(event: Event): void {
  const input = event.target as HTMLInputElement
  const picked = input.files === null ? [] : Array.from(input.files)
  input.value = '' // 清空,允许再次选同一个文件
  if (picked.length > 0) void store.addFiles(picked)
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  const dropped = event.dataTransfer?.files ? Array.from(event.dataTransfer.files) : []
  if (dropped.length > 0) void store.addFiles(dropped)
}

// 回到本页:把上次没跑完的轮询接回来
onMounted(() => {
  store.resumePolling()
})
// 离开本页:必须清掉所有定时器,不留孤儿(HANDOFF §3.1)
onBeforeUnmount(() => {
  store.stopAll()
})
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">导入</h1>
      <p class="page-desc">
        拖进文件或点选,可一次多选;单文件上限 {{ formatBytes(MAX_UPLOAD_BYTES) }},
        前端先挡一轮,后端再串行处理。处理进度每 1 秒自动刷新。
      </p>
    </header>

    <div
      class="dropzone"
      :class="{ 'dropzone--active': dragging }"
      role="button"
      tabindex="0"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
      @click="openPicker"
      @keydown.enter="openPicker"
      @keydown.space.prevent="openPicker"
    >
      <p class="dropzone-title">把文件拖到这里</p>
      <p class="dropzone-hint">或点击选择文件(支持多选,逐个串行上传)</p>
      <button class="btn btn--primary" type="button" @click.stop="openPicker">选择文件</button>
      <input ref="fileInput" class="visually-hidden" type="file" multiple @change="onPicked" />
    </div>

    <p v-if="isBusy" class="uploading-hint">上传中…(串行,一个一个来)</p>

    <ul v-if="items.length > 0" class="file-list">
      <li v-for="item in items" :key="item.key" class="card file-card">
        <div class="file-head">
          <span class="file-name">{{ item.filename }}</span>
          <span class="muted">{{ formatBytes(item.sizeBytes) }}</span>
          <span class="badge" :class="`badge--${itemTone(item)}`">{{ itemLabel(item) }}</span>
        </div>

        <p v-if="item.errorMessage !== null" class="note note--error">{{ item.errorMessage }}</p>

        <template v-if="item.source !== null">
          <div class="file-meta">
            <span class="badge" :class="`badge--${describeParse(item.source).tone}`">
              {{ describeParse(item.source).text }}
            </span>
            <span v-if="item.deduped" class="badge badge--neutral">已存在,未重复入库</span>
            <span v-if="item.source.g2_skipped" class="badge badge--neutral">已跳过归属判断</span>
            <span class="muted mono">sid #{{ item.source.id }}</span>
            <RouterLink
              v-if="item.source.note_id !== null"
              class="file-link"
              :to="`/notes/${item.source.note_id}`"
            >
              查看笔记 #{{ item.source.note_id }}
            </RouterLink>
          </div>

          <p
            v-if="item.source.job.status === 'failed' && item.source.job.last_error !== null"
            class="note note--error"
          >
            失败原因:{{ item.source.job.last_error }}
          </p>

          <ul v-if="item.source.warnings.length > 0" class="note note--warn">
            <li v-for="warning in item.source.warnings" :key="warning">{{ warning }}</li>
          </ul>

          <div class="gates">
            <div v-for="run in item.source.review_runs" :key="run.gate" class="gate">
              <div class="gate-head">
                <span class="gate-name mono">{{ run.gate }}</span>
                <span class="badge" :class="`badge--${verdictTone(run)}`">{{ verdictLabel(run) }}</span>
                <span v-if="run.quality !== null" class="muted">质量:{{ run.quality }}</span>
              </div>
              <ul v-if="run.reasons.length > 0" class="gate-reasons">
                <li v-for="reason in run.reasons" :key="reason">{{ reason }}</li>
              </ul>
              <p v-else class="muted">(这一关没给理由)</p>
            </div>
            <p v-if="item.source.review_runs.length === 0" class="muted">还没有判定记录</p>
          </div>

          <button
            v-if="!isFinished(item)"
            class="btn btn--ghost"
            type="button"
            @click="store.pollNow(item)"
          >
            刷新状态
          </button>
        </template>
      </li>
    </ul>

    <p v-else class="empty">
      还没有导入记录。选几个文件试试 —— 处理进度、每一道的判断结果和理由都会出现在这里。
    </p>
  </section>
</template>

<style scoped>
.dropzone {
  display: flex;
  padding: 30px 24px;
  margin-bottom: 16px;
  border: 1.5px dashed #cfdae6;
  border-radius: var(--radius);
  background: #fbfdff;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.dropzone:hover,
.dropzone--active {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.dropzone-title {
  font-size: 15px;
  font-weight: 600;
}

.dropzone-hint {
  margin-bottom: 10px;
  color: var(--ink-3);
  font-size: 13px;
}

.uploading-hint {
  margin-bottom: 14px;
  color: var(--accent-ink);
  font-size: 13px;
}

.file-list {
  display: flex;
  list-style: none;
  flex-direction: column;
  gap: 12px;
}

.file-card {
  padding: 14px 16px;
}

.file-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.file-name {
  font-weight: 600;
  word-break: break-all;
}

.file-meta {
  display: flex;
  margin-top: 10px;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.file-link {
  font-size: 13px;
}

.gates {
  display: flex;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--line);
  flex-direction: column;
  gap: 8px;
}

.gate-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.gate-name {
  min-width: 28px;
  color: var(--ink-2);
  font-weight: 700;
}

.gate-reasons {
  margin: 4px 0 0;
  padding-left: 18px;
  color: var(--ink-2);
  font-size: 13px;
}
</style>
