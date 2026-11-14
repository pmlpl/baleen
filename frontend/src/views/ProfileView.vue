<script setup lang="ts">
/**
 * 个人中心:GET /api/profile 一次聚合(用户信息 + 使用统计 + 章程预览 + 配置掩码)。
 * 只读展示;编辑动作跳对应页(章程 → /charter,配置 → /settings)。
 * 明确不做的(改密码/邮箱等)见 docs/api.md §7,这里不出入口。
 */
import { computed, onActivated, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getProfile, type Profile } from '../api/profile'
import { describeApiError } from '../api/http'

const router = useRouter()

const profile = ref<Profile | null>(null)
const loading = ref(false)
const errorText = ref('')

async function load(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    profile.value = await getProfile()
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
}

// keep-alive 下 onActivated 初次挂载与每次重新进入都会触发:统计每次进页都拉最新
onActivated(() => {
  void load()
})

const initial = computed(() => (profile.value?.user.username ?? '?').slice(0, 1).toUpperCase())

const registeredAt = computed(() => {
  const iso = profile.value?.user.created_at
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
})

const statsTiles = computed(() => {
  const s = profile.value?.stats
  return [
    { num: s?.notes ?? 0, label: '笔记' },
    { num: s?.sources ?? 0, label: '导入来源' },
    { num: s?.qa_sessions ?? 0, label: '问答会话' },
    { num: s?.qa_messages ?? 0, label: '问答消息' },
  ]
})

/** 预览以「…」结尾 = 后端截断了,给个「看完整」的提示 */
const charterTruncated = computed(() => profile.value?.charter.preview?.endsWith('…') ?? false)

function keyView(set: boolean, tail: string, unsetHint: string): string {
  return set ? `已设置(末四位 ${tail})` : unsetHint
}
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">个人中心</h1>
      <p class="page-desc">账号信息与使用概览。配置修改在设置页,章程编辑在章程向导。</p>
    </header>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <template v-if="profile">
      <div class="card who-card">
        <div class="avatar" aria-hidden="true">{{ initial }}</div>
        <div>
          <div class="username">{{ profile.user.username }}</div>
          <div class="muted">账号 ID {{ profile.user.id }} · 注册于 {{ registeredAt }}</div>
        </div>
      </div>

      <div class="card">
        <h2 class="section-title">使用统计</h2>
        <div class="stats">
          <div v-for="tile in statsTiles" :key="tile.label" class="stat">
            <div class="stat-num">{{ tile.num }}</div>
            <div class="stat-label">{{ tile.label }}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <h2 class="section-title">知识库章程</h2>
        <template v-if="profile.charter.has_charter && profile.charter.preview">
          <pre class="charter-preview">{{ profile.charter.preview }}</pre>
          <p v-if="charterTruncated" class="muted hint">仅显示前 300 字,完整章程在章程页查看。</p>
        </template>
        <p v-else class="muted">还没有章程——填写问卷后,AI 会按你的偏好整理与判定笔记。</p>
        <button class="btn" type="button" @click="router.push({ name: 'charter' })">
          {{ profile.charter.has_charter ? '查看 / 编辑章程' : '去填写章程问卷' }}
        </button>
      </div>

      <div class="card">
        <h2 class="section-title">模型与偏好配置</h2>
        <p class="muted hint">密钥只显示是否已设置与末四位;修改请到设置页。</p>
        <dl class="config-list">
          <div class="config-row">
            <dt>网关地址</dt>
            <dd>{{ profile.settings.llm_base_url }}</dd>
          </div>
          <div class="config-row">
            <dt>模型</dt>
            <dd>{{ profile.settings.llm_model }}</dd>
          </div>
          <div class="config-row">
            <dt>API Key</dt>
            <dd>
              {{ keyView(profile.settings.api_key_set, profile.settings.api_key_tail, '未设置(AI 功能不可用)') }}
            </dd>
          </div>
          <div class="config-row">
            <dt>Tavily 联网搜索</dt>
            <dd>{{ keyView(profile.settings.tavily_set, profile.settings.tavily_tail, '未设置(库外问题不联网)') }}</dd>
          </div>
        </dl>
        <button class="btn" type="button" @click="router.push({ name: 'settings' })">修改配置</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.card {
  max-width: 640px;
  padding: 20px 24px;
  margin-bottom: 16px;
}

.who-card {
  display: flex;
  align-items: center;
  gap: 16px;
}

.avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  font-size: 24px;
  font-weight: 600;
  flex-shrink: 0;
}

.username {
  font-size: 18px;
  font-weight: 600;
}

.section-title {
  margin: 0 0 12px;
  font-size: 16px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.stat {
  padding: 14px 8px;
  border: 1px solid var(--line, #e2e2e2);
  border-radius: 10px;
  text-align: center;
}

.stat-num {
  font-size: 24px;
  font-weight: 700;
}

.stat-label {
  margin-top: 4px;
  color: var(--ink-3, #888);
  font-size: 13px;
}

.charter-preview {
  margin: 0;
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(127, 127, 127, 0.08);
  font-size: 13.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
}

.hint {
  margin: 8px 0;
  font-size: 12.5px;
}

.config-list {
  margin: 0 0 12px;
}

.config-row {
  display: flex;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(127, 127, 127, 0.15);
  font-size: 14px;
}

.config-row:last-child {
  border-bottom: none;
}

.config-row dt {
  flex-shrink: 0;
  width: 120px;
  color: var(--ink-2);
}

.config-row dd {
  margin: 0;
  word-break: break-all;
}

.btn {
  margin-top: 4px;
}
</style>
