<script setup lang="ts">
/**
 * Charter 问卷向导:首次登录自动跳转到这里。
 * 3 步问卷(你是谁 → 收什么/不收什么 → 拿不准+补充) → 第 4 步预览 LLM 生成的章程(可编辑) → 保存。
 * 保存后 G2 热用新章程(DB 优先于 charter.md 文件)。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import { generateCharter, getCharter, saveCharter } from '../api/charter'

defineOptions({ name: 'CharterWizard' })

const router = useRouter()

// 步骤:1=你是谁, 2=收/不收, 3=拿不准+补充, 4=预览
const step = ref(1)
const hasCharter = ref(false)
const generating = ref(false)
const saving = ref(false)
const errorText = ref('')

// Step 1: 你是谁
const direction = ref('')
const purpose = ref('')

// Step 2: 收什么 / 不收什么
const collectOptions = [
  '讲清概念或方法的笔记（定义+例子）',
  '技术文档、论文、项目说明',
  '课程课件与复习整理',
  '问题记录（现象—原因—解决）',
  '手写或拍照的笔记',
]

/** 「不能收」:内容客观上没有价值,固定不收,不给选择(用户没得选,不算偏好) */
const fixedReject = [
  '空文件、hello world、测试文件',
  '壁纸、表情包、明星照片、随手截图',
]

/** 「倾向不收」:边界内容,收不收取决于你自己的用法 —— 默认全不收,取消勾选 = 这类我其实想保留 */
const rejectOptions = [
  '单据与行政表格',
  '无上下文的报错堆栈',
  '纯流程流水账（只写做了什么，没写为什么）',
]
const selectedCollect = ref<string[]>([])
/** 默认全部不收(与 charter.md 默认一致);取消勾选表示「这类我其实想保留」 */
const selectedReject = ref<string[]>([...rejectOptions])

function toggleCollect(item: string): void {
  const idx = selectedCollect.value.indexOf(item)
  if (idx >= 0) selectedCollect.value.splice(idx, 1)
  else selectedCollect.value.push(item)
}

function toggleReject(item: string): void {
  const idx = selectedReject.value.indexOf(item)
  if (idx >= 0) selectedReject.value.splice(idx, 1)
  else selectedReject.value.push(item)
}

// Step 3: 拿不准 + 补充
const unsure = ref('collect_low')
const unsureOptions = [
  { value: 'collect_low', label: '拿不准就收下，标低质量', desc: '错误地拒掉一篇好笔记比收下一份垃圾糟得多——收错了随时能删，拒错了根本不知道丢了什么。' },
  { value: 'reject', label: '拿不准就拒掉', desc: '宁缺毋滥——知识库只放确定有价值的东西。' },
  { value: 'discuss', label: '拿不准先提出来聊', desc: '别直接收也别直接扔，聊完再决定。' },
]
const extra = ref('')

// Step 4: 预览
const generatedCharter = ref('')

const canNext = computed((): boolean => {
  if (step.value === 1) return direction.value.trim() !== ''
  return true
})

async function doGenerate(): Promise<void> {
  generating.value = true
  errorText.value = ''
  try {
    const resp = await generateCharter({
      direction: direction.value.trim(),
      purpose: purpose.value.trim(),
      collect: [...selectedCollect.value],
      reject: [...selectedReject.value],
      unsure: unsure.value,
      extra: extra.value.trim(),
    })
    generatedCharter.value = resp.charter
    step.value = 4
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    generating.value = false
  }
}

async function doSave(): Promise<void> {
  if (saving.value) return
  saving.value = true
  errorText.value = ''
  try {
    await saveCharter(generatedCharter.value)
    router.push({ name: 'import' })
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    saving.value = false
  }
}

function skip(): void {
  router.push({ name: 'import' })
}

onMounted(async () => {
  try {
    const status = await getCharter()
    hasCharter.value = status.has_charter
  } catch {
    // 后端没起来也不挡问卷填写
  }
})
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">知识库章程向导</h1>
      <p class="page-desc">
        填几个问题，AI 帮你生成一份章程——它决定导入时哪些笔记收、哪些不收。保存后立即生效。
      </p>
    </header>

    <p v-if="hasCharter" class="note note--warn">
      你已有一份章程，重新生成将覆盖它。
    </p>
    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <!-- Step 1: 你是谁 -->
    <div v-if="step === 1" class="card wizard-card">
      <p class="step-indicator">第 1 / 3 步 · 你是谁</p>
      <label class="field">
        <span class="field-label">你的专业方向 / 身份</span>
        <input
          v-model="direction"
          class="input"
          type="text"
          placeholder="如：软件工程本科生、前端开发者、数据分析师"
        />
      </label>
      <label class="field">
        <span class="field-label">这个笔记库用来做什么？</span>
        <input
          v-model="purpose"
          class="input"
          type="text"
          placeholder="如：课设、做项目、复习、答辩"
        />
      </label>
      <div class="wizard-actions">
        <button class="btn btn--primary" :disabled="!canNext" @click="step = 2">下一步</button>
        <a class="skip-link" @click="skip">稍后再填</a>
      </div>
    </div>

    <!-- Step 2: 收什么 / 不收什么 -->
    <div v-if="step === 2" class="card wizard-card">
      <p class="step-indicator">第 2 / 3 步 · 收什么 / 不收什么</p>
      <div class="field">
        <span class="field-label">想收什么样的笔记？（多选）</span>
        <div class="chips">
          <button
            v-for="opt in collectOptions"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': selectedCollect.includes(opt) }"
            type="button"
            @click="toggleCollect(opt)"
          >
            {{ opt }}
          </button>
        </div>
      </div>
      <div class="field">
        <span class="field-label">这些默认不收 —— 内容本身没有价值，不需要你选</span>
        <ul class="fixed-reject">
          <li v-for="item in fixedReject" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div class="field">
        <span class="field-label">
          下面这几类是边界情况，收不收看你怎么用 —— 勾选 = 不收，取消勾选 = 这类我其实想保留
        </span>
        <div class="chips">
          <button
            v-for="opt in rejectOptions"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': selectedReject.includes(opt) }"
            type="button"
            @click="toggleReject(opt)"
          >
            {{ opt }}
          </button>
        </div>
      </div>
      <div class="wizard-actions">
        <button class="btn" type="button" @click="step = 1">上一步</button>
        <button class="btn btn--primary" type="button" @click="step = 3">下一步</button>
      </div>
    </div>

    <!-- Step 3: 拿不准 + 补充 -->
    <div v-if="step === 3" class="card wizard-card">
      <p class="step-indicator">第 3 / 3 步 · 拿不准的时候 + 补充</p>
      <div class="field">
        <span class="field-label">遇到拿不准的笔记，你倾向怎么办？</span>
        <div class="radio-list">
          <label
            v-for="opt in unsureOptions"
            :key="opt.value"
            class="radio-option"
            :class="{ 'radio-option--selected': unsure === opt.value }"
          >
            <input
              v-model="unsure"
              type="radio"
              :value="opt.value"
              class="radio-input"
            />
            <span class="radio-label">{{ opt.label }}</span>
            <span class="radio-desc">{{ opt.desc }}</span>
          </label>
        </div>
      </div>
      <label class="field">
        <span class="field-label">还有什么想补充的？（可选）</span>
        <textarea
          v-model="extra"
          class="input textarea"
          rows="3"
          placeholder="如：特别关注某些技术栈、特别在意的规则、自己的一套分类习惯等"
        />
      </label>
      <div class="wizard-actions">
        <button class="btn" type="button" @click="step = 2">上一步</button>
        <button class="btn btn--primary" type="button" :disabled="generating" @click="doGenerate">
          {{ generating ? '生成中…' : '生成章程' }}
        </button>
      </div>
    </div>

    <!-- Step 4: 预览 -->
    <div v-if="step === 4" class="card wizard-card">
      <p class="step-indicator">预览 · 可编辑后保存</p>
      <p class="muted preview-hint">
        下面是 AI 根据你的回答生成的章程，可以直接修改文字，满意后点保存。
      </p>
      <textarea
        v-model="generatedCharter"
        class="charter-preview"
        rows="24"
      />
      <div class="wizard-actions">
        <button class="btn" type="button" @click="step = 3">回去改问卷</button>
        <button class="btn" type="button" @click="doGenerate" :disabled="generating">
          {{ generating ? '重新生成…' : '重新生成' }}
        </button>
        <button class="btn btn--primary" type="button" :disabled="saving" @click="doSave">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.wizard-card {
  max-width: 640px;
  padding: 20px 24px;
}

.step-indicator {
  margin: 0 0 18px;
  color: var(--ink-3);
  font-size: 13px;
  letter-spacing: 0.5px;
}

.field {
  display: block;
  margin-bottom: 18px;
}

.field-label {
  display: block;
  margin-bottom: 8px;
  color: var(--ink-2);
  font-size: 13.5px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.fixed-reject {
  margin: 0;
  padding: 12px 16px 12px 32px;
  border: 1px dashed var(--line);
  border-radius: var(--radius);
  background: var(--neutral-soft);
  color: var(--ink-2);
  font-size: 13.5px;
}

.fixed-reject li + li {
  margin-top: 4px;
}

.chip {
  padding: 7px 14px;
  border: 1px solid var(--line);
  border-radius: 20px;
  cursor: pointer;
  font-size: 13.5px;
  background: var(--panel);
  color: var(--ink-2);
  transition: all 0.15s ease;
  user-select: none;
}

.chip:hover {
  border-color: var(--accent);
}

.chip--selected {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent-ink);
}

.radio-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.radio-option {
  display: block;
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all 0.15s ease;
}

.radio-option:hover {
  border-color: var(--accent);
}

.radio-option--selected {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.radio-input {
  margin-right: 8px;
}

.radio-label {
  font-size: 14px;
  font-weight: 500;
}

.radio-desc {
  display: block;
  margin-top: 4px;
  color: var(--ink-3);
  font-size: 12.5px;
}

.textarea {
  width: 100%;
  resize: vertical;
  font-family: inherit;
}

.charter-preview {
  width: 100%;
  min-height: 360px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.65;
  color: var(--ink);
  background: var(--bg);
  resize: vertical;
}

.preview-hint {
  margin-bottom: 12px;
  font-size: 13px;
}

.wizard-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.skip-link {
  margin-left: auto;
  color: var(--ink-3);
  font-size: 13.5px;
  cursor: pointer;
}

.skip-link:hover {
  color: var(--accent);
  text-decoration: underline;
}

.note--warn {
  background: var(--warn-soft);
  color: var(--warn);
}
</style>
