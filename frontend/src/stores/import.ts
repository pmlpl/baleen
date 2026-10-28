/**
 * 导入队列 + 轮询状态机(docs/api.md §2)。
 *
 * 设计取舍:
 * - 上传串行(一次一个),后端口碑是 max_jobs=1,并发上传只会互相排队,不如前端就把顺序排好;
 * - 轮询用 setTimeout 递归而不是 setInterval —— 请求回来了才排下一次,不会堆积;
 * - 所有定时器都收在 timers 里,页面卸载必须 stopAll(),不留孤儿定时器(HANDOFF §3.1 的教训)。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { describeApiError } from '../api/http'
import { createSource, getSource } from '../api/sources'
import type { JobStatus, SourceDetail } from '../api/sources'

/** 后端上限 50 MB(docs/api.md §2);前端先挡一轮,别让用户白等一次上传 */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

/** job 还在 queued/running 时每 1 秒拉一次(docs/api.md §2「轮询建议」) */
export const POLL_INTERVAL_MS = 1000

/** 兜底上限:合同要求「别无限轮询」,这里给个天花板,超了停手并提示 */
const MAX_POLLS = 300

/** 单个文件在前端的四个阶段:待在队列 → 正在上传 → 后端已收下(之后看 job.status) → 前端侧失败 */
export type FilePhase = 'pending' | 'uploading' | 'accepted' | 'upload-failed'

export interface ImportItem {
  /** 前端本地唯一键(文件名会重复,不能拿文件名当 key) */
  key: string
  filename: string
  sizeBytes: number
  phase: FilePhase
  /** 前端侧错误(超限 / 上传失败 / 轮询失败);后端链路的错误在 source.job.last_error */
  errorMessage: string | null
  sourceId: number | null
  source: SourceDetail | null
  deduped: boolean
  pollCount: number
}

/** 五种 job 状态里哪三种算到站(到站就停轮询;cancelled 也是终态) */
export function jobIsTerminal(status: JobStatus | undefined): boolean {
  return status === 'done' || status === 'failed' || status === 'cancelled'
}

export const useImportStore = defineStore('import', () => {
  const items = ref<ImportItem[]>([])
  /** 是否正在串行上传(给「上传中…」提示用) */
  const isBusy = ref(false)

  /** 待上传的原始 File:故意不放进响应式状态,避免 Vue 去深层代理 File 对象 */
  const pendingFiles = new Map<string, File>()
  /** key → 轮询定时器 */
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  let uploading = false

  function stopPoll(key: string): void {
    const timer = timers.get(key)
    if (timer !== undefined) {
      clearTimeout(timer)
      timers.delete(key)
    }
  }

  /** 页面卸载时调用:停掉所有轮询 */
  function stopAll(): void {
    for (const key of [...timers.keys()]) stopPoll(key)
  }

  function findItem(key: string): ImportItem | undefined {
    return items.value.find((item) => item.key === key)
  }

  function schedulePoll(item: ImportItem): void {
    stopPoll(item.key)
    timers.set(
      item.key,
      setTimeout(() => {
        void pollOnce(item)
      }, POLL_INTERVAL_MS),
    )
  }

  /** 拉一次详情;失败就记人话并返回(调用方据此停止轮询) */
  async function refresh(item: ImportItem): Promise<void> {
    if (item.sourceId === null) return
    try {
      item.source = await getSource(item.sourceId)
      item.errorMessage = null
    } catch (error) {
      item.errorMessage = describeApiError(error)
    }
  }

  async function pollOnce(item: ImportItem): Promise<void> {
    timers.delete(item.key)
    if (findItem(item.key) === undefined) return
    await refresh(item)
    // 出错就停,别每秒钟往页面上刷一条同样的错误
    if (item.errorMessage !== null) return
    if (jobIsTerminal(item.source?.job.status)) return
    item.pollCount += 1
    if (item.pollCount > MAX_POLLS) {
      item.errorMessage = `已轮询 ${MAX_POLLS} 次仍未完成,先停手;可点「刷新状态」再看一眼`
      return
    }
    schedulePoll(item)
  }

  async function uploadOne(item: ImportItem): Promise<void> {
    const file = pendingFiles.get(item.key)
    if (file === undefined) {
      item.phase = 'upload-failed'
      item.errorMessage = '文件句柄已失效,请重新选择该文件'
      return
    }
    item.phase = 'uploading'
    try {
      const accepted = await createSource(file)
      item.sourceId = accepted.source_id
      item.deduped = accepted.deduped
      item.phase = 'accepted'
      // 上传成功后立刻拉一次详情,卡片能马上看到 parse_status 和三道闸
      await refresh(item)
      if (item.errorMessage === null && !jobIsTerminal(item.source?.job.status)) {
        schedulePoll(item)
      }
    } catch (error) {
      item.phase = 'upload-failed'
      item.errorMessage = describeApiError(error)
    } finally {
      pendingFiles.delete(item.key)
    }
  }

  /** 串行泵:pending 的一个一个传,不并发 */
  async function pump(): Promise<void> {
    if (uploading) return
    uploading = true
    isBusy.value = true
    try {
      for (;;) {
        const next = items.value.find((item) => item.phase === 'pending')
        if (next === undefined) break
        await uploadOne(next)
      }
    } finally {
      uploading = false
      isBusy.value = false
      // 极短窗口里又塞进来新文件的话,再推一次,别让它卡在 pending
      if (items.value.some((item) => item.phase === 'pending')) void pump()
    }
  }

  /** 用户选完文件(可多选)后调用 */
  async function addFiles(files: File[]): Promise<void> {
    if (files.length === 0) return
    const stamp = Date.now()
    files.forEach((file, index) => {
      const key = `${stamp}-${index}-${file.name}`
      const item: ImportItem = {
        key,
        filename: file.name,
        sizeBytes: file.size,
        phase: 'pending',
        errorMessage: null,
        sourceId: null,
        source: null,
        deduped: false,
        pollCount: 0,
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        item.phase = 'upload-failed'
        item.errorMessage = '超过 50 MB 上限,前端已拦下,没有发出去'
      } else {
        pendingFiles.set(key, file)
      }
      items.value.push(item)
    })
    await pump()
  }

  /** 回到导入页时把上次没跑完的轮询接回来(已到站的不再排) */
  function resumePolling(): void {
    for (const item of items.value) {
      if (item.phase !== 'accepted') continue
      if (item.errorMessage !== null) continue
      if (jobIsTerminal(item.source?.job.status)) continue
      if (timers.has(item.key)) continue
      schedulePoll(item)
    }
  }

  /** 轮询被停掉之后,给用户一条手动再看一眼的路 */
  async function pollNow(item: ImportItem): Promise<void> {
    stopPoll(item.key)
    item.pollCount = 0
    item.errorMessage = null
    await refresh(item)
    if (item.errorMessage === null && !jobIsTerminal(item.source?.job.status)) {
      schedulePoll(item)
    }
  }

  return { items, isBusy, addFiles, stopAll, resumePolling, pollNow }
})
