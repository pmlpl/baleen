<script setup lang="ts">
/**
 * 纸篓弹窗:点右下角纸篓从页面中央弹出(不切页面)。
 * - 背景朦胧遮罩(压暗 + backdrop blur),点遮罩空白处 / Esc / × 收回
 * - 内容:放回 / 放回全部 / 彻底删除(二次确认,走真删除 API)
 * - 纸篓数据在 stores/trash,放回后书架(keep-alive 的 LibraryView)经响应式过滤自动恢复
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { describeApiError } from '../api/http'
import { deleteNote, getNote } from '../api/notes'
import { useTrashStore } from '../stores/trash'
import { confirmDialog } from '../utils/confirm'
import { spineColor } from '../utils/spine'

interface TrashItem {
  id: number
  title: string
  /** 父块(章节数),给个轻量体量感 */
  chunks: number
}

const emit = defineEmits<{ close: [] }>()

const trash = useTrashStore()
const items = ref<TrashItem[]>([])
const loading = ref(true)
const errorText = ref('')
const workingId = ref<number | null>(null)

/** 逐条拉详情:纸篓通常就几条,不值得为它加批量接口。
 *  拉不到的(已在别处被彻底删除)悄悄出篓,不留幽灵条目 */
async function reload(): Promise<void> {
  loading.value = true
  errorText.value = ''
  const list: TrashItem[] = []
  for (const id of [...trash.ids]) {
    try {
      const detail = await getNote(id)
      list.push({
        id: detail.id,
        title: detail.title,
        chunks: detail.parents.length,
      })
    } catch {
      trash.remove(id)
    }
  }
  items.value = list
  loading.value = false
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

function restore(item: TrashItem): void {
  if (workingId.value !== null) return
  trash.remove(item.id)
  items.value = items.value.filter((v) => v.id !== item.id)
}

/** 手滑批量丢进来时的一键后悔药 */
function restoreAll(): void {
  if (workingId.value !== null) return
  for (const id of [...trash.ids]) trash.remove(id)
  items.value = []
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
    await deleteNote(item.id)
    trash.remove(item.id)
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
        移入纸篓的笔记只记在本浏览器,放回即恢复;彻底删除前它们仍存在于库中(问答检索仍可能命中)。
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
              <span class="muted basket-num">{{ item.chunks }} 段</span>
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
        <p v-else-if="!loading" class="empty basket-empty">
          纸篓是空的。书架上把书拖到左下角书立里的纸篓,或批量选中后「删除所选」,都只是移到这里,可随时放回。
        </p>
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
