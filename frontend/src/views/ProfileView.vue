<script setup lang="ts">
/**
 * 个人中心:GET /api/profile 一次聚合(用户信息 + 章程预览 + 配置掩码)。
 * 只读展示;编辑动作跳对应页(章程 → /charter,配置 → /settings)。
 * 明确不做的(改密码/邮箱等)见 docs/api.md §7,这里不出入口。
 * 布局:通栏两段 —— 头像条 / 章程+模型配置两栏,填满 1060px 书页宽。使用统计已按主人意见删除。
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

// keep-alive 下 onActivated 初次挂载与每次重新进入都会触发:每次进页都拉最新
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

      <div class="grid-2">
        <div class="card">
          <h2 class="section-title">知识库章程</h2>
          <template v-if="profile.charter.has_charter && profile.charter.preview">
            <pre
              class="charter-preview"
              :title="charterTruncated ? '完整章程在章程页查看，此处仅预览前 300 字' : undefined"
              >{{ profile.charter.preview }}</pre>
          </template>
          <button class="btn" type="button" @click="router.push({ name: 'charter' })">
            {{ profile.charter.has_charter ? '查看 / 编辑章程' : '去填写章程问卷' }}
          </button>
        </div>

        <div class="card">
          <h2 class="section-title">模型配置</h2>
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
      </div>
    </template>
  </section>
</template>

<style scoped>
/* 通栏:填满 1060px 书页宽,不再右侧留白 */
.card {
  padding: 20px 24px;
  margin-bottom: 16px;
}

.who-card {
  display: flex;
  align-items: center;
  gap: 16px;
}

/* 印章式头像:墨底纸白字,外面一圈细朱砂环 */
.avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--ink);
  color: var(--panel);
  font-size: 24px;
  font-weight: 600;
  box-shadow: 0 0 0 2px var(--panel), 0 0 0 3.5px var(--seal);
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

/* ---------- 下半:章程 | 模型配置 两栏 ---------- */

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.grid-2 .card {
  margin-bottom: 0;
}

@media (max-width: 900px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }

  .grid-2 .card {
    margin-bottom: 16px;
  }
}

.charter-preview {
  /* 截断预览底部与按钮留一口气(截断提示行砍掉后,这里不再有间距来源) */
  margin: 0 0 10px;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--bg-soft);
  font-size: 13.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}

.config-list {
  margin: 0 0 12px;
}

.config-row {
  display: flex;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
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
