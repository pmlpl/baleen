/**
 * 纸篓的前端状态(v37 起纸篓数据在**后端**)。
 *
 * 分工:
 * - **清单与数量归后端**:`notes.status='archived'`,账号级、跨浏览器、30 天自动清理。
 *   本 store 不存 id 清单,只缓存一个 count 供书立角标显示,真数据由
 *   `GET /api/notes/trash` 提供(BasketModal 自己拉)。
 * - **本 store 只管纯视觉**:飞纸动画期间书架卡片要原地淡出(`flying`)。
 *
 * 为什么不留 id 数组:旧实现把「哪些笔记在纸篓里」存在 localStorage,
 * 导致换浏览器纸篓就空、不同账号共用同一个 key 会互相清空。v37 已改为后端。
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'

import { listTrash } from '../api/notes'

export const useTrashStore = defineStore('trash', () => {
  /** 纸篓里的笔记数量(书立角标用);由 refreshCount() 从后端同步 */
  const count = ref(0)
  /** 正在飞往纸篓途中的 id(书架给这些卡加淡出动画,飞到才算入篓) */
  const flying = ref<number[]>([])

  /** 从后端拉一次纸篓数量。失败保持旧值 —— 角标不准比弹错误横幅轻。 */
  async function refreshCount(): Promise<void> {
    try {
      const page = await listTrash(1, 0)
      count.value = page.total
    } catch {
      /* 拉不到就维持上一次的数字 */
    }
  }

  /** 起飞:书架立刻开始淡出该卡(动画期间卡片还在列表里) */
  function startFly(id: number): void {
    if (!flying.value.includes(id)) flying.value.push(id)
  }

  /** 落篓:淡出动画结束 */
  function endFly(id: number): void {
    flying.value = flying.value.filter((v) => v !== id)
  }

  return { count, flying, refreshCount, startFly, endFly }
})
