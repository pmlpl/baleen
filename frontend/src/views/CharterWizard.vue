<script setup lang="ts">
/**
 * 知识库章程向导。
 *
 * 两种模式:
 * - **首次使用**(DB 无 charter):路由守卫强制停在这里,没有「跳过」,填完才能用其他功能;
 * - **编辑模式**(已有 charter,从设置页进来):直接展示现有章程供修改,也可重新填问卷。
 *
 * 3 步问卷(你是谁 → 收什么/不能收什么 → 拿不准+补充) → 预览可编辑 → 保存。
 * 保存后 G2 热用新章程(DB 优先于 charter.md 文件)。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import { generateCharter, getCharter, saveCharter } from '../api/charter'
import { markCharterSaved } from '../onboardingGate'

defineOptions({ name: 'CharterWizard' })

const router = useRouter()

// 步骤:1=你是谁, 2=收/不收, 3=拿不准+补充, 4=预览
const step = ref(1)
/** 已有章程 = 编辑模式(从设置页来);false = 首次使用,强制填完 */
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

// Step 3/4(扩展):领域 / 来源 / 形态 / 标准
const domainOptions = [
  '前端 / 客户端',
  '后端 / 服务端',
  '数据库',
  'AI / 机器学习',
  '硬件 / 嵌入式',
  '算法 / 数据结构',
  '网络 / 运维',
  '产品 / 设计',
]
const sourceOptions = [
  '课程课件',
  '技术文档 / 论文',
  '视频教程笔记',
  '面试题整理',
  '项目复盘',
  '网页文章剪藏',
  '手写扫描笔记',
]
const formatOptions = [
  '定义 + 例子 + 反例',
  '步骤式教程',
  '问答式',
  '大纲 / 思维导图式',
  '简短速查卡片',
  '对比表格',
]
const qualityOptions = [
  '有代码示例',
  '讲清了原理（为什么）',
  '有踩坑经验',
  '有对比和总结',
  '篇幅精炼',
  '体系完整',
]
const selectedDomains = ref<string[]>([])
const selectedSources = ref<string[]>([])
const selectedFormats = ref<string[]>([])
const selectedQuality = ref<string[]>([])

function toggleDomain(item: string): void {
  const idx = selectedDomains.value.indexOf(item)
  if (idx >= 0) selectedDomains.value.splice(idx, 1)
  else selectedDomains.value.push(item)
}

function toggleSource(item: string): void {
  const idx = selectedSources.value.indexOf(item)
  if (idx >= 0) selectedSources.value.splice(idx, 1)
  else selectedSources.value.push(item)
}

function toggleFormat(item: string): void {
  const idx = selectedFormats.value.indexOf(item)
  if (idx >= 0) selectedFormats.value.splice(idx, 1)
  else selectedFormats.value.push(item)
}

function toggleQuality(item: string): void {
  const idx = selectedQuality.value.indexOf(item)
  if (idx >= 0) selectedQuality.value.splice(idx, 1)
  else selectedQuality.value.push(item)
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
      domains: [...selectedDomains.value],
      sources: [...selectedSources.value],
      formats: [...selectedFormats.value],
      quality_marks: [...selectedQuality.value],
    })
    generatedCharter.value = resp.charter
    step.value = 7
  } catch (error) {
    // 生成失败不把人锁死:转入手写模式,带上问卷内容做起草提示
    errorText.value = `${describeApiError(error)}——可以直接手写章程。`
    generatedCharter.value = draftFromAnswers()
    step.value = 4
  } finally {
    generating.value = false
  }
}

/** 手写章程:跳过 AI,直接进编辑框(只在首次使用时提供) */
function writeManually(): void {
  errorText.value = ''
  generatedCharter.value = draftFromAnswers()
  step.value = 4
}

/** 用问卷答案拼一份草稿骨架,用户在此基础上改(不调 LLM) */
function draftFromAnswers(): string {
  const lines: string[] = ['## 这是谁的库', '']
  if (direction.value.trim() || purpose.value.trim()) {
    lines.push(
      `我是${direction.value.trim() || '（请填写方向）'}，` +
        `这个库用来${purpose.value.trim() || '（请填写用途）'}。`,
    )
  } else {
    lines.push('（请写一段：你是谁、这个库用来做什么）')
  }
  lines.push('', '## 收什么', '')
  if (selectedCollect.value.length > 0) {
    for (const c of selectedCollect.value) lines.push(`- ${c}`)
  } else {
    lines.push('- （请写出你想收的笔记类型，每条带一个例子）')
  }
  lines.push('', '## 不能收什么', '')
  for (const r of fixedReject) lines.push(`- ${r}`)
  lines.push('', '## 不收什么', '')
  if (selectedReject.value.length > 0) {
    for (const r of selectedReject.value) lines.push(`- ${r}`)
  } else {
    lines.push('- （没有特别排除的，可删掉本节）')
  }
  if (selectedSources.value.length > 0) {
    lines.push('', '## 来源偏好', '', `常来自:${selectedSources.value.join('、')}。`)
  }
  if (selectedFormats.value.length > 0) {
    lines.push('', '## 笔记长什么样', '')
    for (const f of selectedFormats.value) lines.push(`- ${f}`)
  }
  lines.push('', '## 拿不准的时候怎么办', '')
  const chosen = unsureOptions.find((o) => o.value === unsure.value)
  lines.push(chosen ? chosen.desc : '拿不准就收下，把质量标低一点。')
  lines.push('', '## 判据', '', '复述别人定的规则不是知识，自己踩坑想通了才是。')
  if (selectedQuality.value.length > 0) {
    lines.push('', `好笔记的标准:${selectedQuality.value.join('；')}。`)
  }
  if (extra.value.trim()) {
    lines.push('', '## 我自己想补的 / 想删的', '', extra.value.trim())
  }
  return lines.join('\n')
}

async function doSave(): Promise<void> {
  if (saving.value) return
  if (generatedCharter.value.trim() === '') {
    errorText.value = '章程不能为空。'
    return
  }
  saving.value = true
  errorText.value = ''
  try {
    await saveCharter(generatedCharter.value)
    markCharterSaved() // 解锁:路由守卫放行其他页面
    router.push({ name: 'qa' })
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    saving.value = false
  }
}

/** 编辑模式下离开(首次使用不提供这个入口) */
function leave(): void {
  router.push({ name: 'settings' })
}

/** 编辑模式下想完全重填:回到第 1 步 */
function restartQuestionnaire(): void {
  step.value = 1
}

onMounted(async () => {
  try {
    const status = await getCharter()
    hasCharter.value = status.has_charter
    if (status.has_charter && status.charter) {
      // 编辑模式:直接展示现有章程,用户改字即可,不必重填问卷
      generatedCharter.value = status.charter
      step.value = 4
    }
  } catch {
    // 查不到就按首次使用走(填完照常能存)
  }
})
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1 class="page-title">{{ hasCharter ? '修改知识库章程' : '先设置你的知识库章程' }}</h1>
      <p class="page-desc">
        章程决定导入时哪些笔记收、哪些不收。
        <template v-if="hasCharter">
          下面是当前的章程，可以直接改文字，也可以重新填一遍问卷。
        </template>
        <template v-else>
          这是开始使用前唯一需要完成的一步，填完即可使用全部功能。
        </template>
      </p>
    </header>

    <p v-if="hasCharter" class="note note--warn">
      修改后立即生效，之后导入笔记时会按新章程判断收还是不收。
    </p>
    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <!-- Step 1: 你是谁 -->
    <div v-if="step === 1" class="card wizard-card">
      <p class="step-indicator">第 1 / 6 步 · 你是谁</p>
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
        <a v-if="hasCharter" class="skip-link" @click="leave">返回设置</a>
      </div>
    </div>

    <!-- Step 2: 收什么 / 不收什么 -->
    <div v-if="step === 2" class="card wizard-card">
      <p class="step-indicator">第 2 / 6 步 · 收什么</p>
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
      <div class="wizard-actions">
        <button class="btn" type="button" @click="step = 1">上一步</button>
        <button class="btn btn--primary" type="button" @click="step = 3">下一步</button>
      </div>
    </div>

    <!-- Step 3: 领域 + 来源偏好 -->
    <div v-if="step === 3" class="card wizard-card">
      <p class="step-indicator">第 3 / 6 步 · 领域与来源</p>
      <div class="field">
        <span class="field-label">主要涉及哪些技术领域？（多选，帮助 AI 判断内容归属）</span>
        <div class="chips">
          <button
            v-for="opt in domainOptions"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': selectedDomains.includes(opt) }"
            type="button"
            @click="toggleDomain(opt)"
          >
            {{ opt }}
          </button>
        </div>
      </div>
      <div class="field">
        <span class="field-label">笔记一般来自哪里？（多选，来源影响「收什么」的判断）</span>
        <div class="chips">
          <button
            v-for="opt in sourceOptions"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': selectedSources.includes(opt) }"
            type="button"
            @click="toggleSource(opt)"
          >
            {{ opt }}
          </button>
        </div>
      </div>
      <div class="wizard-actions">
        <button class="btn" type="button" @click="step = 2">上一步</button>
        <button class="btn btn--primary" type="button" @click="step = 4">下一步</button>
      </div>
    </div>

    <!-- Step 4: 笔记形态 + 好笔记标准 -->
    <div v-if="step === 4" class="card wizard-card">
      <p class="step-indicator">第 4 / 6 步 · 笔记形态与标准</p>
      <div class="field">
        <span class="field-label">你喜欢笔记整理成什么形态？（多选，影响 AI 帮你组织内容的方式）</span>
        <div class="chips">
          <button
            v-for="opt in formatOptions"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': selectedFormats.includes(opt) }"
            type="button"
            @click="toggleFormat(opt)"
          >
            {{ opt }}
          </button>
        </div>
      </div>
      <div class="field">
        <span class="field-label">什么样的笔记对你算「好笔记」？（多选，写进判据）</span>
        <div class="chips">
          <button
            v-for="opt in qualityOptions"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': selectedQuality.includes(opt) }"
            type="button"
            @click="toggleQuality(opt)"
          >
            {{ opt }}
          </button>
        </div>
      </div>
      <div class="wizard-actions">
        <button class="btn" type="button" @click="step = 3">上一步</button>
        <button class="btn btn--primary" type="button" @click="step = 5">下一步</button>
      </div>
    </div>

    <!-- Step 5: 不收什么 -->
    <div v-if="step === 5" class="card wizard-card">
      <p class="step-indicator">第 5 / 6 步 · 不收什么</p>
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
        <button class="btn" type="button" @click="step = 4">上一步</button>
        <button class="btn btn--primary" type="button" @click="step = 6">下一步</button>
      </div>
    </div>

    <!-- Step 3: 拿不准 + 补充 -->
    <div v-if="step === 6" class="card wizard-card">
      <p class="step-indicator">第 6 / 6 步 · 拿不准的时候 + 补充</p>
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
        <button class="btn" type="button" @click="step = 5">上一步</button>
        <button class="btn btn--primary" type="button" :disabled="generating" @click="doGenerate">
          {{ generating ? '生成中…' : '生成章程' }}
        </button>
        <button class="btn btn--ghost" type="button" :disabled="generating" @click="writeManually">
          手写章程
        </button>
      </div>
    </div>

    <!-- Step 4: 预览 / 编辑 -->
    <div v-if="step === 7" class="card wizard-card">
      <p class="step-indicator">{{ hasCharter ? '当前章程 · 可直接编辑' : '预览 · 可编辑后保存' }}</p>
      <p class="muted preview-hint">
        <template v-if="hasCharter">
          下面是当前生效的章程，改完点保存即生效；也可以重新填一遍问卷生成新的。
        </template>
        <template v-else>
          下面是 AI 根据你的回答生成的章程，可以直接修改文字，满意后点保存。
        </template>
      </p>
      <textarea
        v-model="generatedCharter"
        class="charter-preview"
        rows="24"
      />
      <div class="wizard-actions">
        <button class="btn" type="button" @click="restartQuestionnaire">重填问卷</button>
        <button class="btn btn--primary" type="button" :disabled="saving" @click="doSave">
          {{ saving ? '保存中…' : '保存' }}
        </button>
        <a v-if="hasCharter" class="skip-link" @click="leave">返回设置</a>
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
