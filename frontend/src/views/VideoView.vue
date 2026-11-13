<script setup lang="ts">
/**
 * 视频笔记：粘贴 B 站链接 → 看视频 → 选模式 → 导入成笔记。
 *
 * - B 站嵌入式播放器（iframe）让用户可以边看视频边等导入
 * - 两种模式：字幕快速（秒级） / whisper 深度（分钟级，下载音频+whisper转写）
 * - 导入进度面板复用 LibraryView 的模式
 */
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { createSourceFromUrl, getSource } from '../api/sources'
import type { SourceDetail } from '../api/sources'

const BV_RE = /BV[a-zA-Z0-9]{10}/
const PAGE_RE = /[?&]p=(\d+)/

const urlInput = ref('')
const mode = ref<'subtitle' | 'whisper'>('whisper')
const importing = ref(false)

/** 从 URL 提取 BV 号用于嵌入播放器 */
const bvid = computed(() => {
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

/** 是否可以预览（有 BV 号才能嵌入） */
const canPreview = computed(() => bvid.value !== null)

// ---- 导入进度 ----

interface ImportItem {
  stage: string
  status: 'pending' | 'done' | 'failed'
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
  const url = urlInput.value.trim()
  if (url === '' || importing.value) return

  importing.value = true
  imports.value = []

  const item: ImportItem = {
    stage: mode.value === 'whisper' ? '提交中…' : '拉取字幕中',
    status: 'pending',
    noteId: null,
  }
  imports.value.push(item)

  try {
    // API 调用（whisper 模式立即返回，worker 后台提取）
    const upload = await createSourceFromUrl(url, mode.value)

    if (upload.deduped) {
      item.stage = '↩️ 之前导入过,直接复用已有记录'
      item.status = 'done'
      item.noteId = null
    } else {
      item.stage = importStageText(await getSource(upload.source_id))
      const detail = await waitImportDone(upload.source_id, (d) => {
        item.stage = importStageText(d)
      })

      if (detail.job.status === 'failed') {
        item.stage = `❌ 导入失败:${detail.job.last_error ?? '处理失败'}`
        item.status = 'failed'
      } else if (detail.note_id !== null) {
        item.stage = `✅ 已入库,生成笔记 #${detail.note_id}`
        item.status = 'done'
        item.noteId = detail.note_id
      } else {
        item.stage = `✅ 处理完成(状态:${detail.parse_status},没有生成笔记)`
        item.status = 'done'
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
            placeholder="粘贴 B 站视频链接或分享链接..."
            aria-label="B 站视频链接"
          />
        </div>

        <div v-if="canPreview && urlInput.trim() !== ''" class="player-box">
          <iframe
            :src="embedUrl"
            class="player-iframe"
            scrolling="no"
            border="0"
            frameborder="no"
            framespacing="0"
            allowfullscreen="true"
            sandbox="allow-scripts allow-same-origin allow-popups"
          />
        </div>
        <div v-else-if="urlInput.trim() !== ''" class="player-hint muted">
          <p>⚠️ 无法从链接中识别 BV 号。</p>
          <p>短链（b23.tv）无法直接预览，但可以直接点击导入，后端会自动解析。</p>
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
            <label class="mode-option" :class="{ 'mode-option--active': mode === 'subtitle' }">
              <input v-model="mode" type="radio" value="subtitle" />
              <span class="mode-label">字幕快速</span>
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
              <span class="progress-stage" :class="`progress-stage--${it.status}`">{{ it.stage }}</span>
              <RouterLink
                v-if="it.noteId !== null"
                class="progress-link"
                :to="`/notes/${it.noteId}`"
              >
                查看笔记 →
              </RouterLink>
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
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
