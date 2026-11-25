/**
 * 纸篓(Pinia setup 风格,与 stores/auth.ts 一致)——前端级回收站。
 *
 * 桌面回收站隐喻:移入的笔记只是从书架「藏起来」,后端记录原封不动,
 * 可以放回;只有纸篓页里的「彻底删除」才走真删除 API。
 * 注意:篓只记在本浏览器(localStorage `baleen-trash-ids`),换浏览器/清缓存即空。
 */
import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

const STORAGE_KEY = 'baleen-trash-ids'

export const useTrashStore = defineStore('trash', () => {
  /** 在篓里的笔记 id(书架据此隐藏,纸篓页据此列清单) */
  const ids = ref<number[]>([])
  /** 正在飞往纸篓途中的 id(书架给这些卡加淡出动画,飞到才算入篓) */
  const flying = ref<number[]>([])

  // 启动恢复:坏数据/类型不对一律当空篓,不抛错
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw !== null) {
      const parsed = JSON.parse(raw) as unknown
      if (Array.isArray(parsed)) {
        ids.value = parsed.filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
      }
    }
  } catch {
    /* 读不出来就当空篓 */
  }

  // 持久化:任何变更整表写回(几十个 id 的量级,不值得做增量)
  watch(
    ids,
    (value) => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
      } catch {
        /* 记不住就当次会话有效 */
      }
    },
    { deep: true },
  )

  function has(id: number): boolean {
    return ids.value.includes(id)
  }

  /** 入篓(支持批量);已在篓里的自动跳过 */
  function add(list: number | number[]): void {
    for (const id of Array.isArray(list) ? list : [list]) {
      if (!ids.value.includes(id)) ids.value.push(id)
    }
  }

  /** 放回/彻底删除后出篓 */
  function remove(id: number): void {
    ids.value = ids.value.filter((v) => v !== id)
  }

  /** 起飞:书架立刻开始淡出该卡(动画期间卡片还在列表里) */
  function startFly(id: number): void {
    if (!flying.value.includes(id)) flying.value.push(id)
  }

  /** 落篓:淡出动画结束 */
  function endFly(id: number): void {
    flying.value = flying.value.filter((v) => v !== id)
  }

  return { ids, flying, has, add, remove, startFly, endFly }
})
