<script setup lang="ts">
/**
 * 视频笔记：粘贴 B 站 / 抖音链接 → 看视频 → 选模式 → 导入成笔记。
 *
 * - B 站嵌入式播放器（iframe）让用户可以边看视频边等导入
 * - 抖音无嵌入式播放器，显示「在抖音中打开」外链
 * - 两种模式：字幕快速（秒级，仅 B 站） / whisper 深度（分钟级，下载音频+whisper转写）
 * - 导入进度面板复用 LibraryView 的模式
 */
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { createSourceFromUrl, getSource } from '../api/sources'
import type { SourceDetail } from '../api/sources'

const BV_RE = /BV[a-zA-Z0-9]{10}/
const PAGE_RE = /[?&]p=(\d+)/
const DOUYIN_RE = /v\.douyin\.com|www\.douyin\.com|iesdouyin\.com/
// 从分享文本提取真 URL（用户常整段粘贴 "8.48 复制打开抖音… https://v.douyin.com/xxx/ 复制此链接"）
const URL_RE = /https?:\/\/[^\s，。；！？、"'"（）【】<>《》]+/

const urlInput = ref('')
const mode = ref<'subtitle' | 'whisper'>('whisper')
const importing = ref(false)

/** 输入框里的真 URL（分享文本 → 提取第一个链接；纯 URL 原样返回） */
const cleanUrl = computed(() => {
  const raw = urlInput.value.trim()
  if (raw === '') return ''
  const m = URL_RE.exec(raw)
  return m ? m[0] : raw
})

/** 检测抖音链接 */
const isDouyin = computed(() => DOUYIN_RE.test(urlInput.value))

/** 从 URL 提取 BV 号用于嵌入播放器 */
const bvid = computed(() => {
  if (isDouyin.value) return null
  const m = BV_RE.exec(urlInput.value)
  return m ? m[0] : null
})

/** 从 URL 提取分P */
const pageNum = computed(() => {
  const m = PAGE_RE.exec(urlInput.value)
  return m ? m[1] : '1'
})

/** B 站嵌入播放器 URL */
const embedUrl = computed(() => {
  if (!bvid.value) return ''
  return `https://player.bilibili.com/player.html?bvid=${bvid.value}&page=${pageNum.value}&high_quality=1&danmaku=0&autoplay=0`
})

/** 是否可以预览（B站有BV号能嵌入；抖音显示外链） */
const canPreview = computed(() => bvid.value !== null || isDouyin.value)

/** 抖音只支持 whisper 模式，检测到抖音 URL 时自动切换 */
watch(isDouyin, (douyin) => {
  if (douyin) mode.value = 'whisper'
})

// ---- 导入进度 ----

interface ImportItem {
  stage: string
  status: 'pending' | 'done' | 'failed'
  progress: number
  noteId: number | null
}

const imports = ref<ImportItem[]>([])

const IMPORT_POLL_MS = 2000
const IMPORT_MAX_POLLS = 1800 // 2s × 1800 = 60 分钟上限（whisper 异步可能几十分钟）

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

function importStageText(detail: SourceDetail): string {
  // 视频异步提取阶段（下载音频中/whisper转写中/❌错误）——优先显示
  if (detail.video_stage) return detail.video_stage
  if (detail.job.status === 'queued') return '排队中'
  if (detail.job.status === 'running') {
    return detail.parse_status === 'ok' ? 'AI 整理笔记中' : '解析中'
  }
  return '收尾中'
}

// whisper 阶段的插值基准：首次看到 video_est_seconds 时记下本地时间，
// 两次轮询之间进度条也能按「已转写时长 / 预计时长」平滑推进（转写 ~1x 实时）
let whisperStart: { at: number; est: number } | null = null

function computeProgress(detail: SourceDetail): number {
  if (detail.job.status === 'done') return 100
  if (detail.video_progress !== null) {
    // whisper 阶段（30 起、带预计耗时）：按本地时钟插值到 70
    if (
      detail.video_est_seconds !== null &&
      detail.video_progress >= 30 &&
      detail.video_progress < 70
    ) {
      if (whisperStart === null) {
        whisperStart = { at: Date.now(), est: detail.video_est_seconds }
      }
      const elapsed = (Date.now() - whisperStart.at) / 1000
      return Math.min(70, Math.round(30 + (40 * elapsed) / whisperStart.est))
    }
    whisperStart = null
    return detail.video_progress
  }
  // 无视频进度字段（字幕模式/管线阶段）：粗映射
  if (detail.job.status === 'running') return detail.parse_status === 'ok' ? 88 : 80
  return 5
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

async function importVideo(): Promise<void> {
  const url = cleanUrl.value
  if (url === '' || importing.value) return

  importing.value = true
  imports.value = []
  whisperStart = null

  const item: ImportItem = {
    stage: mode.value === 'whisper' ? '提交中…' : '拉取字幕中',
    status: 'pending',
    progress: 2,
    noteId: null,
  }
  imports.value.push(item)

  try {
    // API 调用（whisper 模式立即返回，worker 后台提取）
    const upload = await createSourceFromUrl(url, mode.value)

    if (upload.deduped) {
      item.stage = '↩️ 之前导入过,直接复用已有记录'
      item.status = 'done'
      item.progress = 100
      item.noteId = null
    } else {
      const first = await getSource(upload.source_id)
      item.stage = importStageText(first)
      item.progress = computeProgress(first)
      const detail = await waitImportDone(upload.source_id, (d) => {
        item.stage = importStageText(d)
        item.progress = computeProgress(d)
      })

      if (detail.job.status === 'failed') {
        item.stage = `❌ 导入失败:${detail.job.last_error ?? '处理失败'}`
        item.status = 'failed'
      } else if (detail.note_id !== null) {
        item.stage = `✅ 已入库,生成笔记 #${detail.note_id}`
        item.status = 'done'
        item.progress = 100
        item.noteId = detail.note_id
      } else {
        item.stage = `✅ 处理完成(状态:${detail.parse_status},没有生成笔记)`
        item.status = 'done'
        item.progress = 100
      }
    }
  } catch (error) {
    item.stage = `❌ 导入失败:${describeApiError(error)}`
    item.status = 'failed'
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">视频笔记</h1>
      <p class="page-desc">
        粘贴 B 站视频链接，看视频的同时把它变成可检索的笔记。
      </p>
    </header>

    <div class="video-layout">
      <!-- 左：视频播放器 -->
      <div class="player-section">
        <div class="url-bar">
          <input
            v-model="urlInput"
            class="input"
            type="url"
            placeholder="粘贴 B 站或抖音视频链接..."
            aria-label="视频链接"
          />
        </div>

        <div v-if="canPreview && urlInput.trim() !== ''" class="player-box">
          <iframe
            v-if="bvid"
            :src="embedUrl"
            class="player-iframe"
            scrolling="no"
            border="0"
            frameborder="no"
            framespacing="0"
            allowfullscreen="true"
            sandbox="allow-scripts allow-same-origin allow-popups"
          />
          <div v-else-if="isDouyin" class="douyin-preview">
            <p>🎵 抖音视频</p>
            <a :href="cleanUrl" target="_blank" rel="noopener noreferrer" class="douyin-link">
              在抖音中打开 →
            </a>
            <p class="muted">支持 whisper 转写导入</p>
          </div>
        </div>
        <div v-else-if="urlInput.trim() !== ''" class="player-hint muted">
          <p>⚠️ 无法识别视频链接。</p>
          <p>支持 B 站（BV号/b23.tv）和抖音（v.douyin.com）链接。</p>
        </div>
        <div v-else class="player-hint muted">
          <p>📺 粘贴链接后这里会显示视频播放器，你可以边看视频边等导入完成。</p>
        </div>
      </div>

      <!-- 右：导入设置 + 进度 -->
      <div class="settings-section">
        <div class="card import-card">
          <p class="card-title">导入设置</p>

          <div class="mode-selector">
            <label class="mode-option" :class="{ 'mode-option--active': mode === 'whisper' }">
              <input v-model="mode" type="radio" value="whisper" />
              <span class="mode-label">whisper 深度</span>
              <span class="mode-desc muted">下载音频 + whisper 转写（约 2-3 分钟，内容可靠）</span>
            </label>
            <label class="mode-option" :class="{ 'mode-option--active': mode === 'subtitle', 'mode-option--disabled': isDouyin }">
              <input v-model="mode" type="radio" value="subtitle" :disabled="isDouyin" />
              <span class="mode-label">字幕快速{{ isDouyin ? '（仅 B 站）' : '' }}</span>
              <span class="mode-desc muted">拉 B 站 CC 字幕（秒级，但 B 站字幕不稳定）</span>
            </label>
          </div>

          <button
            class="btn btn--primary import-btn"
            type="button"
            :disabled="urlInput.trim() === '' || importing"
            @click="importVideo"
          >
            {{ importing ? '导入中…' : '导入笔记' }}
          </button>
        </div>

        <!-- 导入进度 -->
        <div v-if="imports.length > 0" class="card progress-card">
          <p class="card-title">导入进度</p>
          <ul class="progress-list">
            <li v-for="(it, i) in imports" :key="i" class="progress-row">
              <div class="progress-track">
                <div
                  class="progress-fill"
                  :class="{ 'progress-fill--failed': it.status === 'failed', 'progress-fill--done': it.status === 'done' }"
                  :style="{ width: it.progress + '%' }"
                />
              </div>
              <div class="progress-meta">
                <span class="progress-stage" :class="`progress-stage--${it.status}`">{{ it.stage }}</span>
                <span v-if="it.status === 'pending'" class="muted progress-pct">{{ it.progress }}%</span>
                <RouterLink
                  v-if="it.noteId !== null"
                  class="progress-link"
                  :to="`/notes/${it.noteId}`"
                >
                  查看笔记 →
                </RouterLink>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.video-layout {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}

.player-section {
  flex: 1;
  min-width: 0;
}

.settings-section {
  width: 340px;
  flex-shrink: 0;
}

.url-bar {
  margin-bottom: 14px;
}

.player-box {
  position: relative;
  width: 100%;
  padding-bottom: 56.25%; /* 16:9 */
  border-radius: 8px;
  overflow: hidden;
  background: #000;
}

.player-iframe {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  border: none;
}

.player-hint {
  padding: 40px 20px;
  text-align: center;
  border: 1px dashed var(--line);
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.8;
}

.progress-track {
  height: 6px;
  margin-bottom: 8px;
  background: var(--line, #e5e7eb);
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #2563eb);
  border-radius: 3px;
  transition: width 0.8s ease;
}

.progress-fill--done {
  background: #22c55e;
}

.progress-fill--failed {
  background: #e5484d;
}

.progress-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.progress-pct {
  font-size: 12.5px;
  min-width: 36px;
  text-align: right;
}

.douyin-preview {
  padding: 40px 20px;
  text-align: center;
  font-size: 14px;
  line-height: 1.8;
}

.douyin-preview p:first-child {
  font-size: 18px;
  margin-bottom: 12px;
}

.douyin-link {
  display: inline-block;
  color: var(--accent, #6c5ce7);
  text-decoration: none;
  font-weight: 600;
  padding: 8px 20px;
  border: 1px solid var(--accent, #6c5ce7);
  border-radius: 6px;
  margin-bottom: 8px;
}

.douyin-link:hover {
  background: var(--accent, #6c5ce7);
  color: #fff;
}

.import-card {
  padding: 16px 18px;
}

.card-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 650;
}

.mode-selector {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}

.mode-option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border: 1.5px solid var(--line);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s;
}

.mode-option--disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.mode-option--active {
  border-color: var(--accent);
}

.mode-option input {
  margin-top: 3px;
  flex-shrink: 0;
}

.mode-label {
  font-size: 14px;
  font-weight: 550;
}

.mode-desc {
  display: block;
  font-size: 12px;
  line-height: 1.5;
  margin-top: 2px;
}

.import-btn {
  width: 100%;
}

.progress-card {
  margin-top: 16px;
  padding: 14px 18px;
}

.progress-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.progress-row {
  display: block;
  padding: 6px 0;
  font-size: 13px;
  line-height: 1.5;
}

.progress-stage {
  flex: 1;
  min-width: 0;
}

.progress-stage--done {
  color: var(--ok, #2f855a);
}

.progress-stage--failed {
  color: var(--danger, #b8474d);
}

.progress-link {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--accent);
}
</style>
