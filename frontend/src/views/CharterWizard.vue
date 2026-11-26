<script setup lang="ts">
/**
 * 知识库章程向导(2026-11-26 问卷事实化)。
 *
 * 两种模式:
 * - **首次使用**(DB 无 charter):路由守卫强制停在这里,填完才能用其他功能;
 * - **编辑模式**(已有 charter,顶栏「知识库章程」进来):直接展示现有章程供修改,也可重新填问卷。
 *
 * 问卷只问**事实**(昵称/年龄段/性别/学历/当前状态/为什么用 baleen)——
 * 偏好(收什么/怎么整理)由 LLM 从事实推导,不再问用户:
 * 旧问卷全是偏好题(「想收什么」「什么算好笔记」),用户面对空库根本答不出来(主人拍板改事实题)。
 * 昵称随章程一起保存,问答时 AI 用它称呼用户;没填就不称呼。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import { generateCharter, getCharter, saveCharter } from '../api/charter'
import { markCharterSaved } from '../onboardingGate'

defineOptions({ name: 'CharterWizard' })

const router = useRouter()

/** 'form' = 填事实问卷;'preview' = 预览/编辑章程文本 */
const step = ref<'form' | 'preview'>('form')
/** 已有章程 = 编辑模式;false = 首次使用,强制填完 */
const hasCharter = ref(false)
const generating = ref(false)
const saving = ref(false)
const errorText = ref('')

// 事实问卷(六题全部可空——没有答不出来的题,没答的 LLM 按通用情况推导)
const nickname = ref('')
const ageBand = ref('')
const gender = ref('')
const education = ref('')
const status = ref('')
const why = ref('')

const ageBands = ['18 岁以下', '18-24', '25-30', '31-40', '40 以上']
const genders = ['男', '女', '不说']
const educations = ['高中及以下', '本科在读', '本科', '硕士及以上']
const statuses = ['在读学生', '在职', '自由职业', '待业', '退休']

const generatedCharter = ref('')

async function doGenerate(): Promise<void> {
  generating.value = true
  errorText.value = ''
  try {
    const resp = await generateCharter({
      nickname: nickname.value.trim(),
      age_band: ageBand.value,
      gender: gender.value,
      education: education.value,
      status: status.value,
      why: why.value.trim(),
    })
    generatedCharter.value = resp.charter
    step.value = 'preview'
  } catch (error) {
    // 生成失败不把人锁死:转手写,带上事实拼的草稿
    errorText.value = `${describeApiError(error)}——可以直接手写章程。`
    generatedCharter.value = draftFromFacts()
    step.value = 'preview'
  } finally {
    generating.value = false
  }
}

/** 手写章程:跳过 AI,直接进编辑框 */
function writeManually(): void {
  errorText.value = ''
  generatedCharter.value = draftFromFacts()
  step.value = 'preview'
}

/** 用事实拼一份草稿骨架,用户在此基础上改(不调 LLM) */
function draftFromFacts(): string {
  const lines: string[] = ['## 这是谁的库', '']
  const name = nickname.value.trim()
  const who = [ageBand.value, education.value, status.value].filter(Boolean).join('·')
  const head = [name ? `「${name}」的库` : '我的库', who ? `我是${who}` : '']
    .filter(Boolean)
    .join('。')
  lines.push(`${head}${head ? '。' : ''}${why.value.trim() ? `用 baleen 来${why.value.trim()}。` : ''}`)
  lines.push('', '## 收什么', '', '- （想收的笔记类型,每条带一个例子）')
  lines.push('', '## 不能收什么', '', '- 空文件、测试文件', '- 壁纸、表情包、随手截图')
  lines.push(
    '',
    '## 拿不准的时候怎么办',
    '',
    '- 拿不准就收下,把质量标低一点——收错了随时能删,拒错了不知道丢了什么。',
  )
  lines.push('', '## 判据', '', '复述别人定的规则不是知识,自己踩坑想通了才是。')
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
    await saveCharter(generatedCharter.value, nickname.value.trim())
    markCharterSaved() // 解锁:路由守卫放行其他页面
    router.push({ name: 'qa' })
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    saving.value = false
  }
}

/** 编辑模式下离开:从哪来回哪去(入口在顶栏,任意页可进) */
function leave(): void {
  router.back()
}

/** 编辑模式下想完全重填:回问卷 */
function restartQuestionnaire(): void {
  step.value = 'form'
}

onMounted(async () => {
  try {
    const s = await getCharter()
    hasCharter.value = s.has_charter
    if (s.nickname) nickname.value = s.nickname
    if (s.has_charter && s.charter) {
      // 编辑模式:直接展示现有章程,用户改字即可,不必重填问卷
      generatedCharter.value = s.charter
      step.value = 'preview'
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
    </header>

    <p v-if="hasCharter" class="note note--warn">
      修改后立即生效，之后导入笔记时会按新章程判断收还是不收。
    </p>
    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <!-- 问卷:六个事实题,一屏放完 -->
    <div v-if="step === 'form'" class="card wizard-card">
      <label class="field">
        <span class="field-label">怎么称呼你</span>
        <input
          v-model="nickname"
          class="input"
          type="text"
          placeholder="问答时 AI 会这样叫你,如:小明"
        />
      </label>

      <div class="field">
        <span class="field-label">年龄段</span>
        <div class="chips">
          <button
            v-for="opt in ageBands"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': ageBand === opt }"
            type="button"
            @click="ageBand = ageBand === opt ? '' : opt"
          >
            {{ opt }}
          </button>
        </div>
      </div>

      <div class="field">
        <span class="field-label">性别</span>
        <div class="chips">
          <button
            v-for="opt in genders"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': gender === opt }"
            type="button"
            @click="gender = gender === opt ? '' : opt"
          >
            {{ opt }}
          </button>
        </div>
      </div>

      <div class="field">
        <span class="field-label">学历</span>
        <div class="chips">
          <button
            v-for="opt in educations"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': education === opt }"
            type="button"
            @click="education = education === opt ? '' : opt"
          >
            {{ opt }}
          </button>
        </div>
      </div>

      <div class="field">
        <span class="field-label">当前状态</span>
        <div class="chips">
          <button
            v-for="opt in statuses"
            :key="opt"
            class="chip"
            :class="{ 'chip--selected': status === opt }"
            type="button"
            @click="status = status === opt ? '' : opt"
          >
            {{ opt }}
          </button>
        </div>
      </div>

      <label class="field">
        <span class="field-label">为什么用 baleen</span>
        <textarea
          v-model="why"
          class="input textarea"
          rows="2"
          placeholder="一句话,如:课程学习、课设沉淀、工作复盘"
        />
      </label>

      <div class="wizard-actions">
        <button class="btn btn--primary" type="button" :disabled="generating" @click="doGenerate">
          {{ generating ? '生成中…' : '生成章程' }}
        </button>
        <button class="btn btn--ghost" type="button" :disabled="generating" @click="writeManually">
          手写章程
        </button>
        <a v-if="hasCharter" class="skip-link" @click="leave">返回</a>
      </div>
    </div>

    <!-- 预览 / 编辑 -->
    <div v-if="step === 'preview'" class="card wizard-card">
      <label class="field">
        <span class="field-label">怎么称呼你</span>
        <input
          v-model="nickname"
          class="input"
          type="text"
          placeholder="问答时 AI 会这样叫你,留空则不称呼"
        />
      </label>
      <label class="field">
        <span class="field-label">章程(导入笔记时 AI 按它整理)</span>
        <textarea v-model="generatedCharter" class="charter-preview" rows="24" />
      </label>
      <div class="wizard-actions">
        <button class="btn btn--primary" type="button" :disabled="saving" @click="doSave">
          {{ saving ? '保存中…' : '保存' }}
        </button>
        <button class="btn" type="button" :disabled="saving" @click="restartQuestionnaire">
          重填问卷
        </button>
        <a v-if="hasCharter" class="skip-link" @click="leave">返回</a>
      </div>
    </div>
  </section>
</template>

<style scoped>
.wizard-card {
  max-width: 640px;
  padding: 20px 24px;
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
