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

const submitLabel = computed(() => {
  if (submitting.value) return mode.value === 'login' ? '登录中…' : '注册中…'
  return mode.value === 'login' ? '登录' : '注册并开始使用'
})
const canSubmit = computed(
  () => username.value.trim().length >= 3 && password.value.length >= 8,
)

async function submit(): Promise<void> {
  if (submitting.value || !canSubmit.value) return
  submitting.value = true
  errorText.value = ''
  try {
    const credentials = { username: username.value.trim(), password: password.value }
    if (mode.value === 'login') await auth.login(credentials)
    else await auth.register(credentials)
    // 登录成功:跳到首页,后续守卫会按「配 key → 填章程」继续引导
    await router.push({ name: 'qa' })
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
    </div>
  </section>
</template>

<style scoped>
.login-page {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 70vh;
  gap: 14px;
}

/* 页脚一抹淡墨山影:blur + 极低不透明度,当远景雾山,不抢卡片。
   fixed 挂视口底部(section 底边会硬裁 SVG);顶部蒙版渐隐防生硬横线 */
.login-page::after {
  content: '';
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  height: 42vh;
  pointer-events: none;
  background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 420' preserveAspectRatio='xMidYMax slice'%3E%3Cfilter id='b'%3E%3CfeGaussianBlur stdDeviation='7'/%3E%3C/filter%3E%3Cg filter='url(%23b)'%3E%3Cpath d='M0 420 L0 302 Q150 176 320 258 T680 240 T1050 282 L1200 238 L1200 420 Z' fill='%23211f1c' fill-opacity='0.045'/%3E%3Cpath d='M0 420 L0 362 Q220 244 460 330 T900 302 L1200 342 L1200 420 Z' fill='%23211f1c' fill-opacity='0.06'/%3E%3C/g%3E%3C/svg%3E")
    bottom / cover no-repeat;
  -webkit-mask-image: linear-gradient(180deg, transparent 0%, #000 55%);
  mask-image: linear-gradient(180deg, transparent 0%, #000 55%);
}

.login-card {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 400px;
  padding: 30px 32px;
  border-radius: 16px;
}

.login-title {
  margin: 0 0 6px;
  font-family: var(--font-serif);
  font-size: 30px;
  font-weight: 700;
  letter-spacing: 0.06em;
}

/* 字标后面的朱砂圆点,和顶栏 brand 同款 */
.login-title::after {
  content: '';
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-left: 9px;
  border-radius: 50%;
  background: var(--seal);
  vertical-align: 5px;
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
  box-shadow: 0 1px 3px rgba(45, 40, 32, 0.1);
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
</style>
