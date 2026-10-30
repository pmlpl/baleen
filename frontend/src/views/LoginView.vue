<script setup lang="ts">
/**
 * 登录 / 注册页:单页双 tab。
 * 成功后跳转由调用方(路由守卫下一次导航)接管,这里只负责拿 token + 提示错误。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import PasswordInput from '../components/PasswordInput.vue'
import { useAuthStore } from '../stores/auth'

defineOptions({ name: 'LoginView' })

const auth = useAuthStore()
const router = useRouter()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const submitting = ref(false)
const errorText = ref('')

const title = computed(() => (mode.value === 'login' ? '登录' : '注册'))
const submitLabel = computed(() => {
  if (submitting.value) return mode.value === 'login' ? '登录中…' : '注册中…'
  return mode.value === 'login' ? '登录' : '注册并开始使用'
})
const canSubmit = computed(
  () => username.value.trim().length >= 3 && password.value.length >= 8,
)

function switchMode(): void {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  errorText.value = ''
}

async function submit(): Promise<void> {
  if (submitting.value || !canSubmit.value) return
  submitting.value = true
  errorText.value = ''
  try {
    const credentials = { username: username.value.trim(), password: password.value }
    if (mode.value === 'login') await auth.login(credentials)
    else await auth.register(credentials)
    // 登录成功:跳到首页,后续守卫会按「配 key → 填章程」继续引导
    await router.push({ name: 'import' })
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="page login-page">
    <div class="card login-card">
      <h1 class="login-title">baleen</h1>
      <p class="muted login-sub">
        个人智能笔记库 —— 导入笔记、检索、问答，都在这一个地方。
      </p>

      <div class="tabs">
        <button
          class="tab"
          :class="{ 'tab--active': mode === 'login' }"
          type="button"
          @click="mode = 'login'"
        >
          登录
        </button>
        <button
          class="tab"
          :class="{ 'tab--active': mode === 'register' }"
          type="button"
          @click="mode = 'register'"
        >
          注册
        </button>
      </div>

      <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

      <form class="form" @submit.prevent="submit">
        <label class="field">
          <span class="field-label">用户名</span>
          <input
            v-model="username"
            class="input"
            type="text"
            autocomplete="username"
            placeholder="3-32 个字符"
          />
        </label>
        <label class="field">
          <span class="field-label">密码</span>
          <PasswordInput
            v-model="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            placeholder="至少 8 个字符"
          />
        </label>
        <button class="btn btn--primary login-submit" type="submit" :disabled="!canSubmit || submitting">
          {{ submitLabel }}
        </button>
      </form>

      <p class="muted login-hint">
        <template v-if="mode === 'login'">还没有账号？</template>
        <template v-else>已经有账号？</template>
        <a @click="switchMode">{{ mode === 'login' ? '去注册' : '去登录' }}</a>
      </p>
    </div>

    <p class="muted login-foot">{{ title }}后即可导入自己的笔记，数据按账号隔离。</p>
  </section>
</template>

<style scoped>
.login-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 70vh;
  gap: 14px;
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: 28px 30px;
}

.login-title {
  margin: 0 0 6px;
  font-size: 24px;
  letter-spacing: 1px;
}

.login-sub {
  margin: 0 0 20px;
  font-size: 13px;
  line-height: 1.6;
}

.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  padding: 3px;
  background: var(--neutral-soft);
  border-radius: 9px;
}

.tab {
  flex: 1;
  padding: 7px 0;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--ink-2);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.tab--active {
  background: var(--panel);
  color: var(--ink);
  font-weight: 500;
  box-shadow: 0 1px 3px rgba(17, 34, 51, 0.08);
}

.form {
  margin: 0;
}

.field {
  display: block;
  margin-bottom: 16px;
}

.field-label {
  display: block;
  margin-bottom: 6px;
  color: var(--ink-2);
  font-size: 13.5px;
}

.login-submit {
  width: 100%;
  margin-top: 4px;
  padding: 9px 0;
}

.login-hint {
  margin: 16px 0 0;
  text-align: center;
  font-size: 13px;
}

.login-hint a {
  margin-left: 4px;
  cursor: pointer;
}

.login-foot {
  font-size: 12.5px;
}
</style>
