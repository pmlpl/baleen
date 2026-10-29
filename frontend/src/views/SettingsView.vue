<script setup lang="ts">
/**
 * 设置页:GET/PUT /api/settings —— 模型与网关配置,保存后热生效(问答/改写即用,无需重启)。
 * api_key 只写不读:后端永不回显全文,只回末 4 位;留空 = 保持不变。
 */
import { onMounted, ref } from 'vue'

import { describeApiError } from '../api/http'
import { getSettings, updateSettings } from '../api/settings'

const baseUrl = ref('')
const model = ref('')
const apiKey = ref('')
const keyTail = ref('')
const keySet = ref(false)
const tavilyKey = ref('')
const tavilyTail = ref('')
const tavilySet = ref(false)
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
    const s = await updateSettings({
      llm_base_url: baseUrl.value.trim() || null,
      llm_api_key: apiKey.value.trim() || null,
      llm_model: model.value.trim() || null,
      tavily_api_key: tavilyKey.value.trim() || null,
    })
    keyTail.value = s.api_key_tail
    keySet.value = s.api_key_set
    tavilyTail.value = s.tavily_tail
    tavilySet.value = s.tavily_set
    apiKey.value = ''
    tavilyKey.value = ''
    saved.value = true
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
      <p class="page-desc">
        模型与网关配置,保存后立即生效(问答/改写热加载,无需重启)。留空 = 保持当前值。
      </p>
    </header>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <div class="card form">
      <label class="field">
        <span class="field-label">网关地址(LLM_BASE_URL)</span>
        <input
          v-model="baseUrl"
          class="input"
          type="text"
          placeholder="https://api.deepseek.com/v1"
        />
      </label>
      <label class="field">
        <span class="field-label">模型(LLM_MODEL)</span>
        <input v-model="model" class="input" type="text" placeholder="deepseek-flash / glm-5.2 …" />
      </label>
      <label class="field">
        <span class="field-label">API Key</span>
        <input
          v-model="apiKey"
          class="input"
          type="password"
          placeholder="留空保持不变"
          autocomplete="off"
        />
        <span v-if="keySet" class="muted key-hint">已设置(末四位 {{ keyTail }})</span>
      </label>
      <label class="field">
        <span class="field-label">Tavily API Key(联网搜索,可选)</span>
        <input
          v-model="tavilyKey"
          class="input"
          type="password"
          placeholder="留空保持不变"
          autocomplete="off"
        />
        <span v-if="tavilySet" class="muted key-hint">已设置(末四位 {{ tavilyTail }})</span>
      </label>
      <button class="btn btn--primary" type="button" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
      <p v-if="saved" class="muted save-ok">已保存,立即生效。</p>
    </div>
  </section>
</template>

<style scoped>
.form {
  max-width: 560px;
  padding: 20px 24px;
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

.key-hint {
  margin-left: 8px;
  font-size: 12.5px;
}

.save-ok {
  margin: 10px 0 0;
}
</style>
