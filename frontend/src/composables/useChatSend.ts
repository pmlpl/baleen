/**
 * 问答页的发送流程(2026-11-26 从 QaView 拆出)。
 *
 * **为什么是 composable 而不是组件**:质检员建议把「SSE 发送循环(~200 行)」拆成
 * 自包含的块,但实测 ``send()`` 只有 154 行,却**读写 16 个父组件状态**
 * (sending / slashOpen / input / sentHistory / historyIndex / draftBeforeRecall /
 * streamingStarted / agentStatus / referencedNotes / attachments / imageAttachments /
 * messages / pendingModel / currentSessionId / errorText / msgListEl)。
 * 照 C2 的「props 进 / emit 出」拆组件要塞 16 个 prop + 十几个 emit,比不拆还乱。
 *
 * 正确形态是 composable:它接收父组件的 ref(共享同一份响应式状态,不做管道),
 * 只把「发送」这一个动作封进来。父组件仍持有全部状态 —— 组件与 composable 的区别
 * 不是「谁持有数据」,而是「逻辑住在哪里」。
 *
 * **依赖注入而非 import**:``startThinkingTimer`` / ``stopThinkingTimer`` /
 * ``scrollBottom`` / ``syncSessionUrl`` / ``startNew`` / ``refreshSessions`` 都是
 * 父组件的局部函数(它们又依赖别的局部状态),由调用方传入,本文件不 import 页面。
 */

import { nextTick, type Ref } from 'vue'

import { describeApiError } from '../api/http'
import { streamChat, type ChatAttachment, type ChatImageAttachment, type QaMessage } from '../api/qa'
import { renderMermaidIn } from '../utils/mermaid'
import { renderMindmapsIn } from '../utils/mindmap'
import { renderSvgsIn } from '../utils/svg'

/** 随问题一起出发的引用/附件快照(气泡上屏用;失败时原样还回输入框) */
export interface SentExtras {
  notes: { id: number; title: string }[]
  files: string[]
  images: { name: string; data_url: string }[]
}

/**
 * 页面用的消息类型:在 API 的 ``QaMessage`` 上补两个**纯前端**字段。
 * - ``search_query``:本轮联网用的检索词(后端 assistant_message 会带,元数据里也有)
 * - ``sent_extras``:发出时带走的引用/附件(后端 user_message 不带 chips,本地捕获)
 * 这两个字段不落库、不进契约,所以不放进 api/qa.ts 的类型里。
 */
export type ChatMessage = QaMessage & { search_query?: string; sent_extras?: SentExtras }

/** 父组件提供给发送流程的一切(全部是共享 ref 或回调,不做 props 管道) */
export interface ChatSendDeps {
  // ── 共享状态 ──
  input: Ref<string>
  sending: Ref<boolean>
  slashOpen: Ref<boolean>
  errorText: Ref<string>
  messages: Ref<ChatMessage[]>
  currentSessionId: Ref<number | null>
  pendingModel: Ref<string | null>
  streamingStarted: Ref<boolean>
  agentStatus: Ref<string>
  sentHistory: Ref<string[]>
  historyIndex: Ref<number>
  referencedNotes: Ref<{ id: number; title: string }[]>
  attachments: Ref<{ name: string; text: string }[]>
  imageAttachments: Ref<{ name: string; mime: string; data_url: string }[]>
  msgListEl: Ref<HTMLElement | null>
  // ── 父组件回调(都是页面局部函数,依赖别的局部状态,只能注入) ──
  startThinkingTimer: () => void
  stopThinkingTimer: () => void
  scrollBottom: () => void
  syncSessionUrl: (id: number | null) => void
  startNew: () => void
  refreshSessions: () => Promise<void>
}

export function useChatSend(deps: ChatSendDeps) {
  const {
    input,
    sending,
    slashOpen,
    errorText,
    messages,
    currentSessionId,
    pendingModel,
    streamingStarted,
    agentStatus,
    sentHistory,
    historyIndex,
    referencedNotes,
    attachments,
    imageAttachments,
    msgListEl,
    startThinkingTimer,
    stopThinkingTimer,
    scrollBottom,
    syncSessionUrl,
    startNew,
    refreshSessions,
  } = deps

  async function send(topK: number): Promise<void> {
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
    const attachmentsPayload: ChatAttachment[] = attachments.value.map((a) => ({
      name: a.name,
      text: a.text,
    }))
    const imagesPayload: ChatImageAttachment[] = imageAttachments.value.map((i) => ({
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
        topK,
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

  return { send }
}
