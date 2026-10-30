<script setup lang="ts">
/**
 * 密码输入框:右侧一个眼睛按钮切换明文/密文,便于检查输错了没有。
 * 支持 v-model;样式沿用全局 .input,额外加右侧内边距给按钮留位。
 */
import { computed, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    placeholder?: string
    autocomplete?: string
  }>(),
  {
    placeholder: '',
    autocomplete: 'current-password',
  },
)
const emit = defineEmits<{ 'update:modelValue': [string] }>()

const visible = ref(false)

const model = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})
</script>

<template>
  <span class="pw-wrap">
    <input
      v-model="model"
      class="input pw-input"
      :type="visible ? 'text' : 'password'"
      :placeholder="placeholder"
      :autocomplete="autocomplete"
    />
    <button
      class="pw-eye"
      type="button"
      :title="visible ? '隐藏密码' : '显示密码'"
      :aria-label="visible ? '隐藏密码' : '显示密码'"
      :aria-pressed="visible"
      @click.stop.prevent="visible = !visible"
    >
      <!-- 睁眼:密码可见中 -->
      <svg
        v-if="visible"
        width="17"
        height="17"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
      <!-- 闭眼:密码隐藏中 -->
      <svg
        v-else
        width="17"
        height="17"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
        <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
        <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" />
        <line x1="1" y1="1" x2="23" y2="23" />
      </svg>
    </button>
  </span>
</template>

<style scoped>
.pw-wrap {
  display: block;
  position: relative;
}

.pw-input {
  padding-right: 38px;
}

.pw-eye {
  position: absolute;
  top: 0;
  right: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 100%;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  border-radius: 0 8px 8px 0;
}

.pw-eye:hover {
  color: var(--accent);
}

.pw-eye:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
</style>
