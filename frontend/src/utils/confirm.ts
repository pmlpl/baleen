/**
 * 全局确认对话框:替代 window.confirm。
 *
 * 为什么不用原生 confirm:它长得跟纸墨主题完全无关(系统灰框),而且在
 * Chromium 里是阻塞式弹窗,自动化探针点不到、Esc 语义也不一致。
 *
 * 用法(无需在组件里挂 <ConfirmDialog> —— 挂载点由 App.vue 统一提供):
 *
 *   const ok = await confirmDialog({ title: '删除这节', body: '…', danger: true, confirmText: '删除' })
 *   if (!ok) return
 *
 * 实现:单例队列。同一时刻只显示一个对话框;`confirm()` 返回 Promise<boolean>。
 * 状态存在响应式对象里,由 App.vue 里那个 <ConfirmDialog> 渲染。
 */
import { reactive, readonly } from 'vue'

export interface ConfirmOptions {
  /** 标题(短,动词短语:删除笔记 / 移入纸篓) */
  title: string
  /** 正文:说清后果。不要写「确定要…吗?」这种废话 */
  body: string
  /** 确认按钮文字,默认「确定」;危险操作建议写具体动作(删除 / 清空) */
  confirmText?: string
  /** 取消按钮文字,默认「取消」 */
  cancelText?: string
  /** 危险操作:确认键用朱砂实底 */
  danger?: boolean
}

interface ConfirmState extends ConfirmOptions {
  open: boolean
  resolve: ((ok: boolean) => void) | null
}

const state = reactive<ConfirmState>({
  open: false,
  title: '',
  body: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
  resolve: null,
})

/** 只读给组件用,避免页面临时改状态 */
export const confirmState = readonly(state)

/** 打开确认框,等用户点确认/取消(或按 Esc / 点遮罩 = 取消) */
export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  // 上一个还没答复就被新请求顶掉:老请求按取消结算,不让它的 await 永久悬着
  state.resolve?.(false)

  return new Promise<boolean>((resolve) => {
    state.open = true
    state.title = options.title
    state.body = options.body
    state.confirmText = options.confirmText ?? '确定'
    state.cancelText = options.cancelText ?? '取消'
    state.danger = options.danger ?? false
    state.resolve = resolve
  })
}

/** 组件内部调用:结算并关闭 */
export function settleConfirm(ok: boolean): void {
  const resolve = state.resolve
  state.resolve = null
  state.open = false
  resolve?.(ok)
}
