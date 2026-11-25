<script setup lang="ts">
/**
 * 个人中心:只展示**别处没有的信息**(账号身份 + 使用统计),其余一律跳转。
 *
 * 主人 2026-11-25 铁律「不重叠功能和内容」——本页原有两块只读副本已删:
 * - 章程预览(前 300 字,截断后不可读)→ 章程真源在 /charter,这里只留跳转；
 * - 模型/网关/Key 只读清单 → 设置页是唯一可编辑真源,改配置还得跳过去,留副本纯属重复。
 * 「使用统计」(笔记/来源/会话/消息)是本页独有信息,保留。
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

      <!-- 章程与配置的**唯一入口**分别在 /charter 与 /settings,这里只给跳转,不放副本 -->
      <div class="card links-card">
        <button class="btn" type="button" @click="router.push({ name: 'charter' })">
          {{ profile.charter.has_charter ? '知识库章程' : '填写章程问卷' }}
        </button>
        <button class="btn" type="button" @click="router.push({ name: 'settings' })">
          模型配置
        </button>
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

/* 跳转区:章程与配置的唯一入口在各自页面,这里只放两个按钮并排。
   不再展示任何只读副本(主人 2026-11-25 铁律:同一信息只在一处)。 */
.links-card {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.btn {
  margin-top: 0;
}
</style>
