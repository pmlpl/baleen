<script setup lang="ts">
/**
 * 视频笔记：粘贴 B 站 / 抖音链接 → 看视频 → 选模式 → 导入成笔记。
 *
 * - B 站嵌入式播放器（iframe）让用户可以边看视频边等导入
 * - 抖音无嵌入式播放器，显示「在抖音中打开」外链
 * - 两种模式：字幕快速（秒级，仅 B 站） / whisper 深度（分钟级，下载音频+whisper转写）
 * - 导入进度面板复用 LibraryView 的模式
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { getBiliLoginStatus, startBiliLogin } from '../api/bilibili'
import type { BiliLoginStatus } from '../api/bilibili'
import { createSourceFromUrl, getSource } from '../api/sources'
import type { SourceDetail, VideoLogEntry } from '../api/sources'

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

/** 抖音链接的站点简称（提示条上显示「v.douyin.com」这类，比整条长链好看） */
const douyinHost = computed(() => {
  try {
    return new URL(cleanUrl.value).hostname
  } catch {
    return 'v.douyin.com'
  }
})

/** 源的 filename 是「视频标题.md」——海报卡上只显示标题本体 */
function stripMdExt(filename: string): string {
  return filename.replace(/\.(md|markdown)$/i, '')
}

/**
 * 抖音封面：只有导入走到提取阶段才有（粘贴那一刻前端只有 URL，拿不到封面）。
 * 取最近一条导入的 —— 一次只会粘一个链接进来。
 */
const douyinCover = computed<string | null>(() => {
  const last = imports.value[imports.value.length - 1]
  return last?.coverUrl ?? null
})

/**
 * 抖音视频标题：导入时源的 filename 就是「标题.md」（后端用视频标题命名），
 * 去掉扩展名直接用。不额外请求、不编造 —— 拿不到就空着不显示。
 * 时长没透出（SourceStatus 没这个字段），所以海报卡不显示时长，宁缺勿造。
 */
const douyinTitle = computed<string>(() => {
  const last = imports.value[imports.value.length - 1]
  if (last === undefined) return ''
  return last.videoTitle
})

/** 抖音只支持 whisper 模式，检测到抖音 URL 时自动切换 */
watch(isDouyin, (douyin) => {
  if (douyin) mode.value = 'whisper'
})

// ---- B 站登录（扫码半自动获取 SESSDATA，导入被风控时的兜底门票）----
// 手动入口：提前扫好/过期重扫；被风控的导入也会在 worker 里自动弹扫码窗
const biliStatus = ref<BiliLoginStatus | null>(null)
const biliLogging = ref(false)
const biliError = ref('')

function formatBiliAt(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function loadBiliStatus(): Promise<void> {
  try {
    biliStatus.value = await getBiliLoginStatus()
  } catch {
    // 状态加载失败不打扰（按钮点了才报错）
  }
}

async function doBiliLogin(): Promise<void> {
  if (biliLogging.value) return
  biliLogging.value = true
  biliError.value = ''
  try {
    // 后端弹真浏览器（Edge）出二维码，同步阻塞等扫码（最长约 3.5 分钟）
    biliStatus.value = await startBiliLogin()
  } catch (error) {
    biliError.value = describeApiError(error)
  } finally {
    biliLogging.value = false
  }
}

// ---- 导入进度 ----

interface ImportItem {
  stage: string
  status: 'pending' | 'done' | 'failed'
  progress: number
  noteId: number | null
  /** 白盒日志：worker/pipeline 各阶段条目，轮询时整体替换触发重渲染 */
  log: VideoLogEntry[]
  /** 封面地址（抖音海报卡用）：导入完成后才有，之前是 null */
  coverUrl: string | null
  /** 视频标题（源自 filename，去掉 .md）：海报卡标题用；拿不到就空串 */
  videoTitle: string
}

const imports = ref<ImportItem[]>([])

const IMPORT_POLL_MS = 2000
const IMPORT_MAX_POLLS = 1800 // 2s × 1800 = 60 分钟上限（whisper 异步可能几十分钟）

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

/** 跟踪断线（轮询连续失败超容忍）——不是导入失败，任务可能仍在 worker 里跑 */
class TrackLostError extends Error {}

function importStageText(detail: SourceDetail): string {
  // 视频异步提取阶段（下载音频中/whisper转写中/❌错误）——优先显示
  if (detail.video_stage) return detail.video_stage
  if (detail.job.status === 'queued') return '排队中'
  if (detail.job.status === 'running') {
    return detail.parse_status === 'ok' ? 'AI 整理笔记中' : '解析中'
  }
  return '收尾中'
}

// whisper 阶段的插值基准：首次看到 video_est_seconds 时按已上报进度反推起点时间，
// 两次轮询之间、甚至刷新页面之后，进度条都能按「已转写时长 / 预计时长」平滑推进
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
        // 反推起点：进度 55% 恢复时 → 等效已转写 (55-30)/40 × est 秒
        const elapsedEq =
          ((detail.video_progress - 30) / 40) * detail.video_est_seconds
        whisperStart = { at: Date.now() - elapsedEq * 1000, est: detail.video_est_seconds }
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

/** epoch 秒 → HH:MM:SS（本地时区），日志区时间戳显示用 */
function formatLogTime(ts: number): string {
  const d = new Date(ts * 1000)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// 进行中的导入持久化到 localStorage（sourceId + 原始链接）：
// 刷新页面后进度面板【和左侧视频/链接预览】全部自动恢复（不再凭空消失）
const ACTIVE_KEY = 'baleen-active-video-import'

function rememberActive(sourceId: number | null, url = ''): void {
  if (sourceId === null) localStorage.removeItem(ACTIVE_KEY)
  else localStorage.setItem(ACTIVE_KEY, JSON.stringify({ sourceId, url }))
}

function readActive(): { sourceId: number; url: string } | null {
  const raw = localStorage.getItem(ACTIVE_KEY)
  if (raw === null) return null
  try {
    const parsed = JSON.parse(raw) as { sourceId?: unknown; url?: unknown }
    const sourceId = typeof parsed.sourceId === 'number' ? parsed.sourceId : NaN
    const url = typeof parsed.url === 'string' ? parsed.url : ''
    if (!Number.isFinite(sourceId) || sourceId <= 0) return null
    return { sourceId, url }
  } catch {
    return null
  }
}

async function waitImportDone(
  sourceId: number,
  onPoll: (detail: SourceDetail) => void,
): Promise<SourceDetail> {
  // 网络闪断/后端热重载的几秒端口空窗 ≠ 任务失败：连续失败超过容忍次数才放弃跟踪。
  // worker 在独立进程继续跑——放弃跟踪只代表「前端看不见了」，不代表「导入失败」。
  const IMPORT_FAIL_TOLERANCE = 6 // 连续 6 次(约 12s+)仍失败才断开跟踪
  let failures = 0
  for (let i = 0; i < IMPORT_MAX_POLLS; i += 1) {
    let detail: SourceDetail
    try {
      detail = await getSource(sourceId)
      failures = 0
    } catch {
      failures += 1
      if (failures >= IMPORT_FAIL_TOLERANCE) throw new TrackLostError()
      await sleep(IMPORT_POLL_MS)
      continue
    }
    if (detail.job.status === 'done' || detail.job.status === 'failed') return detail
    onPoll(detail)
    await sleep(IMPORT_POLL_MS)
  }
  return getSource(sourceId)
}

/** 跟踪一次导入直到终态（新导入和刷新恢复共用）。 */
async function trackImport(sourceId: number, item: ImportItem): Promise<void> {
  const first = await getSource(sourceId)
  item.stage = importStageText(first)
  item.progress = computeProgress(first)
  item.log = first.video_log
  item.coverUrl = first.video_cover_url ?? null
  item.videoTitle = stripMdExt(first.filename)
  const detail = await waitImportDone(sourceId, (d) => {
    item.stage = importStageText(d)
    item.progress = computeProgress(d)
    item.log = d.video_log
    // 封面在提取阶段就写进 parse_meta 了，轮询过程中一旦有就更新海报卡
    item.coverUrl = d.video_cover_url ?? null
    if (d.filename !== '') item.videoTitle = stripMdExt(d.filename)
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
  rememberActive(null)
}

async function importVideo(): Promise<void> {
  const url = cleanUrl.value
  if (url === '' || importing.value) return

  importing.value = true
  imports.value = []
  whisperStart = null

  // reactive 包裹：轮询里 item.stage/progress 的变更才能触发界面重渲染
  // （裸对象塞进 ref 数组后改原始引用会绕过响应式——进度条假死的根因）
  const item = reactive<ImportItem>({
    stage: mode.value === 'whisper' ? '提交中…' : '拉取字幕中',
    status: 'pending',
    progress: 2,
    noteId: null,
    log: [],
    coverUrl: null,
    videoTitle: '',
  })
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
      rememberActive(upload.source_id, url)
      await trackImport(upload.source_id, item)
    }
  } catch (error) {
    if (error instanceof TrackLostError) {
      // 跟踪断线≠导入失败：worker 独立进程里任务还在跑。
      // 保留 rememberActive（不清）——刷新页面可重新接上真实进度。
      item.stage = '⚠️ 跟踪断线（后端重启或网络闪断）——任务可能仍在后台进行，刷新本页可重新接上'
      item.status = 'pending'
    } else {
      item.stage = `❌ 导入失败:${describeApiError(error)}`
      item.status = 'failed'
    }
  } finally {
    importing.value = false
  }
}

// 刷新/重开页面 → 恢复进行中的导入：进度面板【和链接/播放器】全部回来
onMounted(async () => {
  void loadBiliStatus()
  const saved = readActive()
  if (saved === null) return
  urlInput.value = saved.url // 恢复链接 → 播放器/抖音预览/模式判定全部跟着恢复
  try {
    const detail = await getSource(saved.sourceId)
    if (detail.job.status === 'done' || detail.job.status === 'failed') {
      // 已结束：笔记在笔记库里，无需恢复面板
      rememberActive(null)
      return
    }
    whisperStart = null
    const item = reactive<ImportItem>({
      stage: importStageText(detail),
      status: 'pending',
      progress: computeProgress(detail),
      noteId: null,
      log: detail.video_log,
      coverUrl: detail.video_cover_url ?? null,
      videoTitle: stripMdExt(detail.filename),
    })
    imports.value.push(item)
    await trackImport(saved.sourceId, item)
  } catch {
    rememberActive(null)
  }
})
</script>

<template>
  <!-- 投喂台:单列居中。空态=大输入框+模式小卡;粘了链接原位展开播放器+进度 -->
  <section class="page feed">
    <div class="tray" :class="{ 'tray--empty': urlInput.trim() === '' && imports.length === 0 }">
      <div class="tray-bar">
        <input
          v-model="urlInput"
          class="input tray-input"
          type="url"
          placeholder="粘贴 B 站或抖音链接,喂给书房…"
          aria-label="视频链接"
        />
        <button
          class="btn btn--primary tray-import"
          type="button"
          :disabled="urlInput.trim() === '' || importing"
          @click="importVideo"
        >
          {{ importing ? '导入中…' : '导入笔记' }}
        </button>
      </div>

      <div class="tray-row2">
        <div class="mode-switch" role="radiogroup" aria-label="导入模式">
          <span
            class="mode-thumb"
            :class="{ 'mode-thumb--right': mode === 'subtitle' }"
            aria-hidden="true"
          />
          <label class="mode-seg" :class="{ 'mode-seg--on': mode === 'whisper' }">
            <input v-model="mode" type="radio" value="whisper" name="video-mode" />
            whisper 深度
          </label>
          <label
            class="mode-seg"
            :class="{ 'mode-seg--on': mode === 'subtitle', 'mode-seg--off': isDouyin }"
            :title="isDouyin ? '抖音没有 CC 字幕,只能 whisper 深度' : undefined"
          >
            <input v-model="mode" type="radio" value="subtitle" :disabled="isDouyin" name="video-mode" />
            字幕快速
          </label>
        </div>

        <!-- B 站登录:SESSDATA 是被风控时浏览器兜底的门票;平时不用登录,收成一行小字 -->
        <p class="tray-bili muted">
        <template v-if="biliStatus?.logged_in">
          B 站已登录{{ biliStatus.uid ? `(${biliStatus.uid})` : ''
          }}<template v-if="biliStatus.logged_at"> · 上次扫码 {{ formatBiliAt(biliStatus.logged_at) }}</template>
        </template>
        <template v-else>B 站未登录(平时不用登录,被风控时才需要)</template>
        <button
          class="bili-link"
          type="button"
          :disabled="biliLogging"
          title="被风控时导入会自动弹出扫码窗,也可提前扫码备用"
          @click="doBiliLogin"
        >
          {{ biliLogging ? '等待扫码中…' : biliStatus?.logged_in ? '重新扫码' : '扫码登录' }}
          </button>
        </p>
      </div>
      <p v-if="biliError" class="bili-error">❌ {{ biliError }}</p>

    </div>

    <!-- 粘了链接:原位展开播放器/预览 -->
    <!-- B 站:真 iframe 播放器,16:9 黑框是正确的(iframe 加载前不闪白) -->
    <div v-if="bvid" class="player-box">
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

    <!-- 抖音:不支持网页内嵌播放,**不能复用 player-box** ——
         那是给 iframe 留 16:9 的黑底容器,抖音塞进去就是一个 850×480 的黑洞里
         飘两行小字(主人 2026-11-26 反馈「这是一个黑色的,我觉得不好看」)。
         改成轻量提示条;拿到封面后升级成海报卡。 -->
    <div v-else-if="isDouyin" class="douyin-preview">
      <!-- 封面海报卡:要等导入完成才有(粘贴那一刻前端只有 URL,拿不到封面) -->
      <div v-if="douyinCover !== null" class="douyin-poster">
        <img :src="douyinCover" class="poster-img" alt="" loading="lazy" referrerpolicy="no-referrer" />
        <div class="poster-body">
          <p class="poster-kicker">抖音视频</p>
          <p v-if="douyinTitle !== ''" class="poster-title">{{ douyinTitle }}</p>
          <a :href="cleanUrl" target="_blank" rel="noopener noreferrer" class="douyin-link">
            在抖音中打开 ↗
          </a>
        </div>
      </div>

      <!-- 无封面(粘贴瞬间 / 平台没给封面 / 存量笔记):一行高的提示条 -->
      <div v-else class="douyin-strip">
        <span class="strip-icon" aria-hidden="true">♪</span>
        <span class="strip-text">
          <span class="strip-host">{{ douyinHost }}</span>
          <span class="strip-hint">抖音不支持网页内播放;导入后自动转写 + 抽帧,这段等待不用盯</span>
        </span>
        <a :href="cleanUrl" target="_blank" rel="noopener noreferrer" class="douyin-link">
          在抖音中打开 ↗
        </a>
      </div>
    </div>

    <div v-else-if="urlInput.trim() !== ''" class="player-hint muted">
      <p>⚠️ 无法识别视频链接。</p>
      <p>支持 B 站(BV号/b23.tv)和抖音(v.douyin.com)链接。</p>
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
          <ul v-if="it.log.length > 0" class="progress-log">
            <li
              v-for="(entry, j) in it.log"
              :key="j"
              class="progress-log-item"
            >
              <span class="progress-log-ts muted">{{ formatLogTime(entry.ts) }}</span>
              <span class="progress-log-stage">{{ entry.stage }}</span>
              <span v-if="entry.pct !== null" class="progress-log-pct muted">{{ entry.pct }}%</span>
            </li>
          </ul>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
/* 投喂台:单列居中,内容列压到 860 书页宽(舞台虽是 1440 宽档,页面自己收) */
.feed {
  max-width: 860px;
  margin: 0 auto;
}

.tray {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 6px 0 4px;
}

/* 空态 = 整页主角:投喂台抬高居中,输入框放大居中文字 */
.tray--empty {
  padding: 56px 0 12px;
}

.tray--empty .tray-input {
  height: 44px;
  font-size: 15px;
}

/* 第一行:链接输入 + 导入钮同行,整行与播放器同宽 */
.tray-bar {
  display: flex;
  gap: 10px;
}

.tray-input {
  flex: 1;
  min-width: 0;
}

.tray-import {
  flex-shrink: 0;
}

.tray-row2 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

/* 模式切换:胶囊分段控件,墨色滑块点击滑动(radio 隐身只管语义)。
   两段 grid 等宽,滑块 width=50%-3px 恰好对齐任意一段 */
.mode-switch {
  position: relative;
  display: grid;
  flex-shrink: 0;
  grid-template-columns: 1fr 1fr;
  padding: 3px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--bg-soft);
}

.mode-thumb {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 3px;
  width: calc(50% - 3px);
  border-radius: 999px;
  background: var(--ink);
  box-shadow: 0 1px 3px rgba(45, 40, 32, 0.25);
  transition: transform 0.18s ease;
}

.mode-thumb--right {
  transform: translateX(100%);
}

.mode-seg {
  position: relative;
  z-index: 1;
  padding: 6px 18px;
  border-radius: 999px;
  color: var(--ink-2);
  font-size: 13px;
  font-weight: 550;
  text-align: center;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.15s ease;
}

.mode-seg input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.mode-seg--on {
  color: var(--panel);
}

.mode-seg--off {
  opacity: 0.4;
  cursor: not-allowed;
}

/* B 站登录:一行小字 + 文字链按钮,平时不打扰 */
.tray-bili {
  display: flex;
  margin: 0;
  align-items: baseline;
  justify-content: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12.5px;
  line-height: 1.6;
}

.bili-link {
  padding: 0;
  border: none;
  background: none;
  color: var(--accent);
  font-size: 12.5px;
  cursor: pointer;
}

.bili-link:hover:not(:disabled) {
  text-decoration: underline;
}

.bili-error {
  margin: 0;
  color: var(--err);
  font-size: 12.5px;
  text-align: center;
}

.player-box {
  position: relative;
  width: 100%;
  margin-top: 18px;
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
  background: var(--line);
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent-ink));
  border-radius: 3px;
  transition: width 0.8s ease;
}

.progress-fill--done {
  background: var(--ok);
}

.progress-fill--failed {
  background: var(--err);
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

/* ── 抖音:没有内嵌播放器,所以**绝不套 player-box 黑底** ──
   主人 2026-11-26 反馈「这是一个黑色的,不好看」—— 根因是抖音复用了给 iframe
   留 16:9 的黑底容器,变成一个 850×480 的黑洞里飘两行小字。
   现在两种形态:有封面 → 海报卡;没封面 → 一行高的提示条。 */

.douyin-preview {
  margin-top: 18px;
}

/* 提示条:一行高、纸面底色、左侧一道黛青竖线当作「视频」的视觉锚点 */
.douyin-strip {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px 12px 16px;
  border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  background: var(--panel);
}

.strip-icon {
  flex-shrink: 0;
  color: var(--accent);
  font-size: 16px;
}

.strip-text {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.strip-host {
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.strip-hint {
  overflow: hidden;
  color: var(--ink-3);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 海报卡:封面在左(固定 16:9 小图),信息在右 */
.douyin-poster {
  display: flex;
  align-items: stretch;
  gap: 16px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel);
}

/* 抖音是竖屏平台（实测封面 1080×1920），用 9:16 竖版 ——
   按 16:9 裁会切掉大半个标题和人物，等于把封面糟蹋了 */
.poster-img {
  flex-shrink: 0;
  width: 112px;
  aspect-ratio: 9 / 16;
  border-radius: 6px;
  object-fit: cover;
  background: var(--bg-soft);
}

.poster-body {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  justify-content: center;
  gap: 8px;
}

.poster-kicker {
  margin: 0;
  color: var(--ink-3);
  font-size: 11.5px;
  letter-spacing: 0.06em;
}

.poster-title {
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 15px;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.douyin-link {
  align-self: flex-start;
  margin-top: 2px;
  padding: 6px 14px;
  border: 1px solid var(--accent);
  border-radius: 6px;
  color: var(--accent);
  font-size: 12.5px;
  font-weight: 600;
  text-decoration: none;
  transition: all 0.15s ease;
}

.douyin-link:hover {
  color: #fff;
  background: var(--accent);
}

/* 窄屏:海报卡改竖排,封面撑满宽度 */
@media (max-width: 720px) {
  .douyin-poster {
    flex-direction: column;
  }

  .poster-img {
    width: 100%;
  }
}

.card-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 650;
}

.progress-card {
  margin-top: 18px;
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
  color: var(--ok);
}

.progress-stage--failed {
  color: var(--err);
}

.progress-log {
  max-height: 96px;
  overflow-y: auto;
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
  border-left: 2px solid var(--line);
  font-size: 12px;
}

.progress-log-item {
  display: flex;
  gap: 6px;
  padding: 1px 0 1px 8px;
}

.progress-log-ts {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.progress-log-stage {
  flex: 1;
  min-width: 0;
}

.progress-link {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--accent);
}
</style>
