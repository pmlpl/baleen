<script setup lang="ts">
/**
 * 全局确认对话框(单例,由 App.vue 挂一次)。
 * 状态与 Promise 结算在 utils/confirm.ts,本组件只负责渲染与键盘。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { confirmState, settleConfirm } from '../utils/confirm'

/** 确认键:打开后自动聚焦,回车即确认(危险操作也照此,文字已说清后果) */
const confirmBtn = ref<HTMLButtonElement | null>(null)

watch(
  () => confirmState.open,
  (open) => {
    if (open) void nextTick(() => confirmBtn.value?.focus())
  },
)

function onKeydown(event: KeyboardEvent): void {
  if (!confirmState.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    settleConfirm(false)
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div v-if="confirmState.open" class="confirm-overlay" @click.self="settleConfirm(false)">
    <section class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="cf-title">
      <h2 id="cf-title" class="confirm-title">{{ confirmState.title }}</h2>
      <p class="confirm-body">{{ confirmState.body }}</p>
      <div class="confirm-actions">
        <button
          class="btn confirm-cancel"
          type="button"
          @click="settleConfirm(false)"
        >
          {{ confirmState.cancelText }}
        </button>
        <button
          ref="confirmBtn"
          class="btn"
          :class="confirmState.danger ? 'confirm-danger' : 'btn--primary'"
          type="button"
          @click="settleConfirm(true)"
        >
          {{ confirmState.confirmText }}
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  /* 高于纸篓(1150)、低于飞纸(1200):确认框永远在最上层的语义层 */
  z-index: 1300;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(28, 26, 22, 0.42);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
}

.confirm-dialog {
  width: min(420px, 100%);
  padding: 22px 24px 18px;
  border: 1px solid var(--line-strong);
  border-radius: 14px;
  background: var(--panel);
  box-shadow: 0 18px 60px rgba(28, 26, 22, 0.35);
  animation: confirm-in 0.16s ease;
}

@keyframes confirm-in {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.985);
  }

  to {
    opacity: 1;
    transform: none;
  }
}

.confirm-title {
  margin: 0 0 8px;
  font-family: var(--font-serif);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

/* 后果说明:正常墨色,不压灰 —— 这是用户真正要读的一句 */
.confirm-body {
  margin: 0 0 20px;
  color: var(--ink-2);
  font-size: 13.5px;
  line-height: 1.75;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.confirm-cancel {
  min-width: 72px;
}

/* 危险确认:朱砂实底,和「彻底删除」同一语言 */
.confirm-danger {
  min-width: 96px;
  border-color: var(--seal);
  background: var(--seal);
  color: #fff;
  font-weight: 600;
}

.confirm-danger:hover:not(:disabled) {
  filter: brightness(1.06);
}
</style>
