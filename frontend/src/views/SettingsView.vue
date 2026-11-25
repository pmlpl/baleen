<script setup lang="ts">
/**
 * 设置页:GET/PUT /api/settings —— 模型与网关配置,保存后热生效(问答/改写即用,无需重启)。
 * api_key 只写不读:后端永不回显全文,只回末 4 位;留空 = 保持不变。
 * 布局:通栏两栏(模型服务 | 联网与生成)+ 底部保存行,填满 1060px 书页宽。
 */
import { onMounted, ref } from 'vue'

import { describeApiError } from '../api/http'
import { getSettings, updateSettings } from '../api/settings'
import PasswordInput from '../components/PasswordInput.vue'
import { markKeyConfigured } from '../onboardingGate'

const baseUrl = ref('')
const model = ref('')
const apiKey = ref('')
const keyTail = ref('')
const keySet = ref(false)
const tavilyKey = ref('')
const tavilyTail = ref('')
const tavilySet = ref(false)
const contextTokens = ref('')
const outputTokens = ref('')
const allowDiagrams = ref(true)

const loading = ref(false)
const saving = ref(false)
const errorText = ref('')
const saved = ref(false)

async function load(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    const s = await getSettings()
    baseUrl.value = s.llm_base_url
    model.value = s.llm_model
    keyTail.value = s.api_key_tail
    keySet.value = s.api_key_set
    tavilyTail.value = s.tavily_tail
    tavilySet.value = s.tavily_set
    contextTokens.value = s.llm_context_tokens != null ? String(s.llm_context_tokens) : ''
    outputTokens.value = s.llm_max_output_tokens != null ? String(s.llm_max_output_tokens) : ''
    allowDiagrams.value = s.allow_diagrams
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (saving.value) return
  saving.value = true
  errorText.value = ''
  saved.value = false
  try {
    const ctx = contextTokens.value.trim()
    const out = outputTokens.value.trim()
    const s = await updateSettings({
      llm_base_url: baseUrl.value.trim() || null,
      llm_api_key: apiKey.value.trim() || null,
      llm_model: model.value.trim() || null,
      tavily_api_key: tavilyKey.value.trim() || null,
      // 空 = 保持不变;0 = 清空回退兜底;正数 = 用户填的上限
      llm_context_tokens: ctx === '' ? null : Number(ctx),
      llm_max_output_tokens: out === '' ? null : Number(out),
      allow_diagrams: allowDiagrams.value,
    })
    keyTail.value = s.api_key_tail
    keySet.value = s.api_key_set
    tavilyTail.value = s.tavily_tail
    tavilySet.value = s.tavily_set
    apiKey.value = ''
    tavilyKey.value = ''
    saved.value = true
    // 通知闸门:配好 key 后可以进入下一步(章程)
    markKeyConfigured(s.api_key_set)
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">设置</h1>
      <p class="page-desc">留空 = 保持当前值。</p>
    </header>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <div class="grid-2">
      <div class="card">
        <h2 class="section-title">模型服务</h2>
        <label class="field">
          <span class="field-label" title="对应后端配置 LLM_BASE_URL">网关地址</span>
          <input
            v-model="baseUrl"
            class="input"
            type="text"
            placeholder="https://api.deepseek.com/v1"
          />
        </label>
        <label class="field">
          <span class="field-label" title="对应后端配置 LLM_MODEL">模型</span>
          <input v-model="model" class="input" type="text" placeholder="deepseek-flash / glm-5.2 …" />
        </label>
        <label class="field field--last">
          <span class="field-label">API Key</span>
          <PasswordInput
            v-model="apiKey"
            placeholder="留空保持不变"
            autocomplete="off"
          />
          <span v-if="keySet" class="muted key-hint">已设置(末四位 {{ keyTail }})</span>
        </label>
      </div>

      <div class="card">
        <h2 class="section-title">联网与生成</h2>
        <label class="field">
          <span class="field-label">Tavily API Key(联网搜索,可选)</span>
          <PasswordInput
            v-model="tavilyKey"
            placeholder="留空保持不变"
            autocomplete="off"
          />
          <span v-if="tavilySet" class="muted key-hint">已设置(末四位 {{ tavilyTail }})</span>
        </label>
        <label class="field">
          <span class="field-label">上下文上限(可选,token)</span>
          <input
            v-model="contextTokens"
            class="input"
            type="number"
            min="0"
            placeholder="填模型上下文长度，留空=整篇喂"
          />
        </label>
        <label class="field">
          <span class="field-label">输出上限(可选,token)</span>
          <input
            v-model="outputTokens"
            class="input"
            type="number"
            min="0"
            placeholder="填模型最大输出，留空=131072"
          />
        </label>
        <label
          class="field field--switch field--last"
          title="仅当内容是并列清单或流程阶段时才画(系统按标题结构判定)；节点对不上原文的图会被自动丢弃。关掉则一律不生成"
        >
          <input v-model="allowDiagrams" type="checkbox" class="switch" />
          <span class="field-label">导入时生成结构图</span>
        </label>
      </div>
    </div>

    <div class="actions">
      <button class="btn btn--primary" type="button" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
      <p v-if="saved" class="muted save-ok">已保存,立即生效。</p>
    </div>
  </section>
</template>

<style scoped>
/* .field(display:block,声明在后)会盖掉单类选择器,这里必须双类提特异性 */
.field.field--switch {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.field--switch .switch {
  align-self: flex-start;
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
  cursor: pointer;
}

/* ---------- 通栏两栏:模型服务 | 联网与生成 ---------- */

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.section-title {
  margin: 0 0 14px;
  font-size: 16px;
}

.card {
  padding: 20px 24px;
}

/* 底部通栏操作行:保存 + 保存提示 */
.actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
}

.field {
  display: block;
  margin-bottom: 16px;
}

/* 卡内最后一个字段不吃底边距,免得把卡撑高一截空 */
.field--last {
  margin-bottom: 0;
}

.field-label {
  display: block;
  margin-bottom: 6px;
  color: var(--ink-2);
  font-size: 13.5px;
}

.key-hint {
  margin-left: 8px;
  font-size: 12.5px;
}

.save-ok {
  margin: 0;
}

@media (max-width: 900px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }
}
</style>
