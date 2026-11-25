<script setup lang="ts">
/**
 * 纸篓弹窗:点右下角纸篓从页面中央弹出(不切页面)。
 *
 * v37 起纸篓是**账号级**的后端数据(notes.status='archived'),不再是本浏览器
 * localStorage 的一份 id 清单 —— 换浏览器/换设备纸篓还在,不同账号互不可见。
 * 保留期 30 天(从移入那一刻起算),到期由后端 arq 每日清理真正删除。
 *
 * 弹窗职责:列清单(带剩余天数)/ 放回 / 放回全部 / 彻底删除(二次确认,真删)。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { listTrash, purgeNote, restoreNote } from '../api/notes'
import type { TrashItem } from '../api/notes'
import { confirmDialog } from '../utils/confirm'
import { spineColor } from '../utils/spine'

const emit = defineEmits<{ close: [] }>()

const items = ref<TrashItem[]>([])
const loading = ref(true)
const errorText = ref('')
const workingId = ref<number | null>(null)
/** 后端给的保留期(天);没拿到时按 30 兜底,避免界面出现 undefined */
const retentionDays = ref(30)

/** 剩余天数文案:0 天单独说「今天到期」,比「还剩 0 天」明确 */
function daysLabel(days: number): string {
  return days <= 0 ? '今天到期' : `剩 ${days} 天`
}

/** 快到期(3 天内)用朱砂提醒,其余走灰墨 */
function isUrgent(days: number): boolean {
  return days <= 3
}

const emptyHint = computed(
  () =>
    `纸篓是空的。书架把书拖到左下角的纸篓、或批量选中后「删除所选」,都只是移到这里;` +
    `移入后 ${retentionDays.value} 天内可随时放回。`,
)

async function reload(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    const page = await listTrash()
    items.value = page.items
    retentionDays.value = page.retention_days
  } catch (error) {
    errorText.value = describeApiError(error)
    items.value = []
  } finally {
    loading.value = false
  }
}

// 弹窗每次打开都是新挂载(v-if),直接拉一遍最新清单
void reload()

function close(): void {
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') close()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

/** 放回一篇:后端置回 active,本地从清单移除 —— 书架(LibraryView)靠 onActivated 重新拉列表 */
async function restore(item: TrashItem): Promise<void> {
  if (workingId.value !== null) return
  workingId.value = item.id
  errorText.value = ''
  try {
    await restoreNote(item.id)
    items.value = items.value.filter((v) => v.id !== item.id)
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    workingId.value = null
  }
}

/** 手滑批量丢进来时的一键后悔药 */
async function restoreAll(): Promise<void> {
  if (workingId.value !== null || items.value.length === 0) return
  workingId.value = -1 // 占位:表示「批量放回中」,禁用全部按钮
  errorText.value = ''
  const failed: string[] = []
  for (const item of [...items.value]) {
    try {
      await restoreNote(item.id)
      items.value = items.value.filter((v) => v.id !== item.id)
    } catch (error) {
      failed.push(`${item.title}:${describeApiError(error)}`)
    }
  }
  workingId.value = null
  if (failed.length > 0) errorText.value = `有 ${failed.length} 篇没能放回 —— ${failed[0]}`
}

/** 彻底删除:真删除、不可恢复,所以必须二次确认 */
async function purge(item: TrashItem): Promise<void> {
  if (workingId.value !== null) return
  const label = item.title === '(无标题)' || item.title === '' ? `#${item.id}` : `《${item.title}》`
  const ok = await confirmDialog({
    title: `彻底删除${label}`,
    body: '正文、切块、向量和原文件会一起清掉,不可恢复。',
    confirmText: '彻底删除',
    danger: true,
  })
  if (!ok) return
  workingId.value = item.id
  errorText.value = ''
  try {
    await purgeNote(item.id)
    items.value = items.value.filter((v) => v.id !== item.id)
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    workingId.value = null
  }
}
</script>

<template>
  <!-- 朦胧遮罩:压暗 + 毛玻璃;只有点在遮罩自身(而非弹窗)才收回 -->
  <div class="basket-overlay" @click.self="close">
    <section class="basket-dialog" role="dialog" aria-modal="true" aria-label="纸篓">
      <header class="basket-dialog-head">
        <h2 class="basket-dialog-title">纸篓</h2>
        <span class="muted basket-count">
          {{ loading ? '清点中…' : `${items.length} 本待处理` }}
        </span>
        <button
          class="btn btn--ghost basket-restore-all"
          type="button"
          :disabled="workingId !== null || items.length === 0"
          @click="restoreAll"
        >
          放回全部
        </button>
        <button class="basket-close" type="button" title="收起" aria-label="收起纸篓" @click="close">
          ×
        </button>
      </header>

      <p class="basket-hint">
        纸篓对当前账号生效(换浏览器也在);移入满 {{ retentionDays }} 天后自动彻底删除,期间可随时放回。
      </p>

      <p v-if="errorText !== ''" class="note note--error basket-error">{{ errorText }}</p>

      <div class="basket-body">
        <ul v-if="items.length > 0" class="basket-list">
          <li v-for="item in items" :key="item.id" class="basket-row">
            <RouterLink
              class="basket-link"
              :to="`/notes/${item.id}`"
              :style="{ '--spine': spineColor(item.id) }"
              @click="close"
            >
              <span class="basket-bar" />
              <span class="basket-title" :title="item.title">{{ item.title }}</span>
              <span class="muted basket-num">{{ item.chunk_count }} 段</span>
              <!-- 剩余天数:0 天 = 今天到期。快到期转朱砂,不用另写一句提示 -->
              <span
                class="basket-days"
                :class="{ 'basket-days--urgent': isUrgent(item.days_left) }"
                :title="`${item.expires_at.slice(0, 10)} 自动彻底删除`"
              >
                {{ daysLabel(item.days_left) }}
              </span>
            </RouterLink>
            <span class="basket-acts">
              <button
                class="btn btn--ghost"
                type="button"
                :disabled="workingId !== null"
                title="放回书架"
                @click="restore(item)"
              >
                放回
              </button>
              <button
                class="btn btn--danger"
                type="button"
                :disabled="workingId !== null"
                title="真删除,不可恢复"
                @click="purge(item)"
              >
                {{ workingId === item.id ? '删除中…' : '彻底删除' }}
              </button>
            </span>
          </li>
        </ul>
        <p v-else-if="!loading" class="empty basket-empty">{{ emptyHint }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.basket-overlay {
  position: fixed;
  inset: 0;
  /* 纸篓本体(1100)之上、飞纸(1200)之下 */
  z-index: 1150;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(28, 26, 22, 0.42);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
}

.basket-dialog {
  display: flex;
  width: min(640px, 100%);
  max-height: min(76vh, 640px);
  flex-direction: column;
  border: 1px solid var(--line-strong);
  border-radius: 14px;
  background: var(--panel);
  box-shadow: 0 18px 60px rgba(28, 26, 22, 0.35);
  overflow: hidden;
  animation: basket-dialog-in 0.18s ease;
}

@keyframes basket-dialog-in {
  from {
    opacity: 0;
    transform: translateY(10px) scale(0.98);
  }

  to {
    opacity: 1;
    transform: none;
  }
}

.basket-dialog-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px 10px;
  border-bottom: 1px solid var(--line);
}

.basket-dialog-title {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.basket-count {
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}

.basket-restore-all {
  margin-left: auto;
}

.basket-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  background: none;
  color: var(--ink-3);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}

.basket-close:hover {
  color: var(--ink);
}

.basket-hint {
  margin: 10px 18px 0;
  color: var(--ink-3);
  font-size: 12.5px;
  line-height: 1.6;
}

.basket-error {
  margin: 10px 18px 0;
}

.basket-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 18px 16px;
}

.basket-list {
  display: flex;
  margin: 0;
  padding: 0;
  list-style: none;
  flex-direction: column;
  gap: 8px;
}

.basket-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 10px 6px 0;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fffdf8;
  transition:
    border-color 0.13s ease,
    box-shadow 0.13s ease;
}

.basket-row:hover {
  border-color: var(--line-strong);
  box-shadow: var(--shadow);
}

.basket-link {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 12px;
  color: inherit;
}

.basket-link:hover {
  text-decoration: none;
}

/* 书脊色条延续隐喻:和书架上是同一本书 */
.basket-bar {
  width: 4px;
  align-self: stretch;
  border-radius: 2px;
  background: var(--spine);
  opacity: 0.9;
}

.basket-title {
  overflow: hidden;
  min-width: 0;
  flex: 1;
  font-size: 13.5px;
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.basket-num {
  flex-shrink: 0;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 剩余天数:常态灰墨,3 天内转朱砂(紧迫感靠颜色,不靠一句提示) */
.basket-days {
  flex-shrink: 0;
  color: var(--ink-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.basket-days--urgent {
  color: var(--seal);
  font-weight: 600;
}

.basket-acts {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}

.basket-empty {
  margin: 8px 0;
}
</style>
