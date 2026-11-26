<script setup lang="ts">
/**
 * 我的画像(2026-11-26,由「知识库章程」页扩写而成)。
 *
 * 左右两栏,一静一动:
 * - **左 = 我是谁**:问卷五项事实(昵称/年龄段/性别/学历/当前状态/为什么用)
 *   + 据此推导的章程。全部原地可编辑 —— 改完按一次保存,不用重走问卷向导。
 * - **右 = 我在做什么**:系统从真实数据数出来的统计。每次打开现算,永远是最新的。
 *
 * **统计不注入问答**:主人拍板先只看效果,所以这批不落库摘要、不改 prompt。
 * 不注入就没有「摘要多久重算一次」的问题,也就不存在陈旧。
 *
 * 三分口径(别写错):库内答 = 有 note 引用;联网答 = 有 url 引用;
 * 自答 = 两者都没有 —— 只有自答才是「库该补的内容」。零引用不等于盲区,
 * 其中大部分走了联网,那是功能在正常工作。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { describeApiError } from '../api/http'
import {
  analyzeTopics,
  generateCharter,
  getPersona,
  getPersonaStats,
  savePersona,
  type Persona,
  type PersonaFacts,
  type PersonaStats,
} from '../api/persona'
import { markCharterSaved } from '../onboardingGate'

defineOptions({ name: 'PersonaView' })

const router = useRouter()

const loading = ref(true)
const saving = ref(false)
const analyzing = ref(false)
const errorText = ref('')
const savedHint = ref('')

/** 左栏:事实 + 章程 */
const facts = ref<PersonaFacts>({
  nickname: '',
  age_band: '',
  gender: '',
  education: '',
  status: '',
  why: '',
})
const charter = ref('')
const hasCharter = ref(false)
/** 首次使用(无章程)走问卷向导流程;之后是原地编辑 */
const firstTime = ref(false)
const generatedPreview = ref('')
const generating = ref(false)

/** 右栏:统计 */
const stats = ref<PersonaStats | null>(null)

const ageBands = ['18 岁以下', '18-24', '25-30', '31-40', '40 以上']
const genders = ['男', '女', '不说']
const educations = ['高中及以下', '本科在读', '本科', '硕士及以上']
const statuses = ['在读学生', '在职', '自由职业', '待业', '退休']

/** 事实行的展示:没答过就显示「未填」 */
const factRows = computed(() => [
  { key: 'age_band', label: '年龄段', value: facts.value.age_band, options: ageBands },
  { key: 'gender', label: '性别', value: facts.value.gender, options: genders },
  { key: 'education', label: '学历', value: facts.value.education, options: educations },
  { key: 'status', label: '当前状态', value: facts.value.status, options: statuses },
] as const)

/** 规模那行的紧凑展示 */
const scaleLine = computed(() => {
  const s = stats.value
  if (s === null) return ''
  return `${s.size.notes} 篇 · ${s.size.chunks} 块 · ${s.size.sessions} 次对话 · ${s.size.asks} 次提问`
})

/** 来源构成:视频 vs 文档 */
const sourceBars = computed(() => {
  const s = stats.value
  if (s === null) return []
  const total = Object.values(s.sources).reduce((a, b) => a + b, 0)
  if (total === 0) return []
  return [
    { label: '视频', count: s.sources.video ?? 0, total },
    { label: '文档', count: s.sources.doc ?? 0, total },
  ]
})

/** 质量构成 */
const qualityBars = computed(() => {
  const s = stats.value
  if (s === null) return []
  const total = Object.values(s.quality).reduce((a, b) => a + b, 0)
  if (total === 0) return []
  return [
    { label: '核心', count: s.quality.core ?? 0, total, tone: 'core' },
    { label: '普通', count: s.quality.normal ?? 0, total, tone: 'normal' },
    { label: '参考', count: s.quality.reference ?? 0, total, tone: 'reference' },
  ].filter((b) => b.count > 0)
})

/** 回答方式三分:三段堆叠条 + 百分比 */
const answerSegments = computed(() => {
  const s = stats.value
  if (s === null || s.answers.total === 0) return []
  const t = s.answers.total
  return [
    { key: 'kb', label: '库内答', count: s.answers.kb, pct: Math.round((s.answers.kb / t) * 100) },
    { key: 'web', label: '联网答', count: s.answers.web, pct: Math.round((s.answers.web / t) * 100) },
    { key: 'self', label: '自答', count: s.answers.self, pct: Math.round((s.answers.self / t) * 100) },
  ]
})

/** 活跃度:找出最大值做归一,零周不画柱 */
const activityBars = computed(() => {
  const s = stats.value
  if (s === null || s.activity.length === 0) return []
  const max = Math.max(1, ...s.activity.map((w) => w.notes))
  // 固定像素高而非百分比:父槽是 flex:1(高度由 flex 撑开,不是确定值),
  // 子元素 height:% 解析不到基准 → 实测最高那根只画出 20%。给确定数值才准。
  const CHART_H = 72
  return s.activity.map((w) => ({
    ...w,
    // 只留月/日,标签不至于挤成一团
    label: w.week.slice(5).replace('-', '/'),
    px: Math.max(Math.round((w.notes / max) * CHART_H), 3),
  }))
})

const topicBars = computed(() => {
  const s = stats.value
  if (s === null || s.topics.length === 0) return []
  const max = Math.max(...s.topics.map((t) => t.notes))
  return s.topics.map((t) => ({ ...t, width: Math.round((t.notes / max) * 100) }))
})

async function loadAll(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    const [persona, s] = await Promise.all([getPersona(), getPersonaStats()])
    applyPersona(persona)
    stats.value = s
    // 无章程 = 首次使用:直接进问卷(引导期这个页面就是必经步骤)
    firstTime.value = !persona.has_charter
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    loading.value = false
  }
}

function applyPersona(persona: Persona): void {
  facts.value = {
    nickname: persona.nickname ?? '',
    age_band: persona.age_band ?? '',
    gender: persona.gender ?? '',
    education: persona.education ?? '',
    status: persona.status ?? '',
    why: persona.why ?? '',
  }
  charter.value = persona.charter ?? ''
  hasCharter.value = persona.has_charter
}

async function refreshStats(): Promise<void> {
  try {
    stats.value = await getPersonaStats()
  } catch {
    // 统计失败不打断左栏使用:右栏保持上一次的内容
  }
}

async function doSave(): Promise<void> {
  if (saving.value) return
  if (firstTime.value && generatedPreview.value.trim() === '') {
    errorText.value = '章程不能为空。'
    return
  }
  saving.value = true
  errorText.value = ''
  savedHint.value = ''
  try {
    const persona = await savePersona({
      charter: firstTime.value ? generatedPreview.value : charter.value,
      ...facts.value,
    })
    applyPersona(persona)
    markCharterSaved() // 解锁:路由守卫放行其他页面
    if (firstTime.value) {
      firstTime.value = false
      await refreshStats()
    }
    savedHint.value = '已保存'
    window.setTimeout(() => (savedHint.value = ''), 2000)
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    saving.value = false
  }
}

/** 首次使用:手写章程骨架(不调 LLM) */
async function writeManually(): Promise<void> {
  generatedPreview.value = draftFromFacts()
}

function draftFromFacts(): string {
  const lines: string[] = ['## 这是谁的库', '']
  const name = facts.value.nickname.trim()
  const who = [facts.value.age_band, facts.value.education, facts.value.status].filter(Boolean).join('·')
  const head = [name ? `「${name}」的库` : '我的库', who ? `我是${who}` : ''].filter(Boolean).join('。')
  lines.push(`${head}。${facts.value.why.trim() ? `用 baleen 来${facts.value.why.trim()}。` : ''}`)
  lines.push('', '## 收什么', '', '- （想收的笔记类型,每条带一个例子）')
  lines.push('', '## 不能收什么', '', '- 空文件、测试文件', '- 壁纸、表情包、随手截图')
  lines.push('', '## 拿不准的时候怎么办', '', '- 拿不准就收下,把质量标低一点——收错了随时能删,拒错了不知道丢了什么。')
  lines.push('', '## 判据', '', '复述别人定的规则不是知识,自己踩坑想通了才是。')
  return lines.join('\n')
}

async function doGenerate(): Promise<void> {
  generating.value = true
  errorText.value = ''
  try {
    const resp = await generateCharter({
      nickname: facts.value.nickname.trim(),
      age_band: facts.value.age_band,
      gender: facts.value.gender,
      education: facts.value.education,
      status: facts.value.status,
      why: facts.value.why.trim(),
    })
    generatedPreview.value = resp.charter
  } catch (error) {
    errorText.value = `${describeApiError(error)}——可以直接手写章程。`
    generatedPreview.value = draftFromFacts()
  } finally {
    generating.value = false
  }
}

/** 主题分析:首次分析全部未打标笔记,已打过则问是否重打 */
async function doAnalyze(retag = false): Promise<void> {
  if (analyzing.value) return
  analyzing.value = true
  errorText.value = ''
  try {
    const resp = await analyzeTopics(retag)
    if (stats.value !== null) {
      stats.value = { ...stats.value, topics: resp.topics, untagged: resp.remaining }
    }
    await refreshStats()
  } catch (error) {
    errorText.value = describeApiError(error)
  } finally {
    analyzing.value = false
  }
}

function leave(): void {
  router.back()
}

onMounted(loadAll)
</script>

<template>
  <section class="page persona-page">
    <header class="page-head">
      <h1 class="page-title">{{ firstTime ? '先认识一下' : '我的画像' }}</h1>
    </header>

    <p v-if="errorText !== ''" class="note note--error">{{ errorText }}</p>

    <div v-if="loading" class="persona-loading">加载中…</div>

    <div v-else class="persona-grid">
      <!-- ─────────── 左:我是谁(静态·可编辑) ─────────── -->
      <div class="persona-col persona-col--static">
        <div class="col-head">
          <span class="col-kicker">我是谁</span>
        </div>

        <!-- 首次使用:问卷(全部可空,不答就按通用情况推导) -->
        <template v-if="firstTime">
          <div class="card fact-card">
            <label class="field">
              <span class="field-label">怎么称呼你</span>
              <input v-model="facts.nickname" class="input" type="text" placeholder="AI 会这样叫你,如:小明" />
            </label>

            <div v-for="row in factRows" :key="row.key" class="field">
              <span class="field-label">{{ row.label }}</span>
              <div class="chips">
                <button
                  v-for="opt in row.options"
                  :key="opt"
                  class="chip"
                  :class="{ 'chip--selected': facts[row.key] === opt }"
                  type="button"
                  @click="facts[row.key] = facts[row.key] === opt ? '' : opt"
                >
                  {{ opt }}
                </button>
              </div>
            </div>

            <label class="field">
              <span class="field-label">为什么用 baleen</span>
              <textarea v-model="facts.why" class="input textarea" rows="2" placeholder="一句话,如:课程学习、课设沉淀、工作复盘" />
            </label>

            <div class="actions">
              <button class="btn btn--primary" type="button" :disabled="generating" @click="doGenerate">
                {{ generating ? '生成中…' : '生成章程' }}
              </button>
              <button class="btn btn--ghost" type="button" :disabled="generating" @click="writeManually">
                手写章程
              </button>
            </div>
          </div>

          <label v-if="generatedPreview !== ''" class="field">
            <span class="field-label">章程(导入笔记时 AI 按它整理)</span>
            <textarea v-model="generatedPreview" class="charter-box" rows="18" />
          </label>
          <div v-if="generatedPreview !== ''" class="actions">
            <button class="btn btn--primary" type="button" :disabled="saving" @click="doSave">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </template>

        <!-- 已填过:原地可编辑 -->
        <template v-else>
          <div class="fact-list">
            <label class="fact-row">
              <span class="fact-key">昵称</span>
              <input v-model="facts.nickname" class="fact-input" type="text" placeholder="未填" />
            </label>
            <label v-for="row in factRows" :key="row.key" class="fact-row">
              <span class="fact-key">{{ row.label }}</span>
              <select v-model="facts[row.key]" class="fact-input fact-select">
                <option value="">未填</option>
                <option v-for="opt in row.options" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </label>
            <label class="fact-row fact-row--wide">
              <span class="fact-key">为什么用</span>
              <input v-model="facts.why" class="fact-input" type="text" placeholder="未填" />
            </label>
          </div>

          <div class="col-head col-head--sub">
            <span class="col-kicker">据此推导的章程</span>
          </div>
          <textarea v-model="charter" class="charter-box" rows="20" spellcheck="false" />

          <div class="actions">
            <button class="btn btn--primary" type="button" :disabled="saving" @click="doSave">
              {{ saving ? '保存中…' : '保存' }}
            </button>
            <span v-if="savedHint !== ''" class="saved-hint">{{ savedHint }}</span>
            <a class="leave-link" @click="leave">返回</a>
          </div>
        </template>
      </div>

      <!-- ─────────── 右:我在做什么(动态·系统算) ─────────── -->
      <div class="persona-col persona-col--live">
        <div class="col-head">
          <span class="col-kicker">我在做什么</span>
          <span class="col-meta">{{ scaleLine }}</span>
        </div>

        <!-- 来源构成 -->
        <section v-if="sourceBars.length > 0" class="stat-block">
          <h3 class="stat-title">来源</h3>
          <div class="bar-row">
            <div v-for="b in sourceBars" :key="b.label" class="bar-item">
              <span class="bar-label">{{ b.label }}</span>
              <span class="bar-track">
                <span class="bar-fill bar-fill--source" :style="{ width: `${(b.count / b.total) * 100}%` }" />
              </span>
              <span class="bar-count">{{ b.count }}</span>
            </div>
          </div>
        </section>

        <!-- 质量构成 -->
        <section v-if="qualityBars.length > 0" class="stat-block">
          <h3 class="stat-title">质量</h3>
          <div class="bar-row">
            <div v-for="b in qualityBars" :key="b.label" class="bar-item">
              <span class="bar-label">{{ b.label }}</span>
              <span class="bar-track">
                <span class="bar-fill" :class="`bar-fill--${b.tone}`" :style="{ width: `${(b.count / b.total) * 100}%` }" />
              </span>
              <span class="bar-count">{{ b.count }}</span>
            </div>
          </div>
        </section>

        <!-- 回答方式三分 -->
        <section v-if="answerSegments.length > 0" class="stat-block">
          <h3 class="stat-title">回答方式</h3>
          <div class="stack-bar">
            <span
              v-for="seg in answerSegments"
              :key="seg.key"
              class="stack-seg"
              :class="`stack-seg--${seg.key}`"
              :style="{ flexGrow: Math.max(seg.count, 0.0001) }"
              :title="`${seg.label} ${seg.count} 次`"
            />
          </div>
          <ul class="legend">
            <li v-for="seg in answerSegments" :key="seg.key" class="legend-item">
              <span class="legend-dot" :class="`legend-dot--${seg.key}`" />
              <span class="legend-label">{{ seg.label }}</span>
              <span class="legend-pct">{{ seg.pct }}%</span>
            </li>
          </ul>
        </section>

        <!-- 主题分布 -->
        <section class="stat-block">
          <h3 class="stat-title">
            主题
            <button
              v-if="stats !== null && (stats.untagged > 0 || stats.topics.length === 0)"
              class="btn btn--tiny"
              type="button"
              :disabled="analyzing"
              @click="doAnalyze(false)"
            >
              {{ analyzing ? '分析中…' : `分析 ${stats.untagged} 篇的主题` }}
            </button>
            <button
              v-else-if="stats !== null && stats.topics.length > 0"
              class="btn btn--tiny btn--ghost"
              type="button"
              :disabled="analyzing"
              @click="doAnalyze(true)"
            >
              {{ analyzing ? '重打中…' : '重新分析' }}
            </button>
          </h3>
          <div v-if="topicBars.length > 0" class="bar-row">
            <div v-for="t in topicBars" :key="t.topic" class="bar-item">
              <span class="bar-label">{{ t.topic }}</span>
              <span class="bar-track">
                <span class="bar-fill bar-fill--topic" :style="{ width: `${t.width}%` }" />
              </span>
              <span class="bar-count">{{ t.notes }}</span>
            </div>
          </div>
          <p v-else class="stat-empty">还没分析过主题。</p>
        </section>

        <!-- 真在用的知识 -->
        <section v-if="stats !== null && stats.top_cited.length > 0" class="stat-block">
          <h3 class="stat-title">真在用的知识</h3>
          <ol class="rank-list">
            <li v-for="(n, i) in stats.top_cited" :key="n.id" class="rank-item">
              <span class="rank-no">{{ i + 1 }}</span>
              <RouterLink class="rank-title" :to="{ name: 'note', params: { id: n.id } }">{{ n.title }}</RouterLink>
              <span class="rank-hits">{{ n.hits }} 次</span>
            </li>
          </ol>
        </section>

        <!-- 死库存 -->
        <section v-if="stats !== null && stats.dead_notes.total > 0" class="stat-block">
          <h3 class="stat-title">
            没被用过的笔记
            <span class="stat-count">{{ stats.dead_notes.total }}</span>
          </h3>
          <ul class="plain-list">
            <li v-for="n in stats.dead_notes.items" :key="n.id" class="plain-item">
              <RouterLink class="plain-link" :to="{ name: 'note', params: { id: n.id } }">{{ n.title }}</RouterLink>
            </li>
          </ul>
        </section>

        <!-- 纯自答的问题 -->
        <section v-if="stats !== null && stats.self_answered.length > 0" class="stat-block">
          <h3 class="stat-title">我也没答上的问题</h3>
          <ul class="plain-list">
            <li v-for="(q, i) in stats.self_answered" :key="i" class="plain-item plain-item--quote">{{ q }}</li>
          </ul>
        </section>

        <!-- 活跃度 -->
        <section v-if="activityBars.length > 0" class="stat-block">
          <h3 class="stat-title">每周新增</h3>
          <div class="week-chart">
            <div v-for="w in activityBars" :key="w.week" class="week-col" :title="`${w.week}：${w.notes} 篇`">
              <!-- 柱高给像素值:父槽是 flex:1,百分比高度解析不到基准 -->
              <span class="week-slot">
                <span class="week-bar" :style="{ height: `${w.px}px` }" />
              </span>
              <span class="week-label">{{ w.label }}</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  </section>
</template>

<style scoped>
.persona-page {
  padding-bottom: 32px;
}

.persona-loading {
  padding: 40px 0;
  color: var(--ink-3);
  font-size: 13.5px;
}

/* 左右两栏:左静态(事实) / 右动态(统计)。窄屏退成单列,不硬挤 */
.persona-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr);
  gap: 40px;
  align-items: start;
}

@media (max-width: 980px) {
  .persona-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 32px;
  }
}

.persona-col {
  min-width: 0;
}

/* 左栏跟着滚:右栏很长(七块统计),左栏太短会在下面留一大片空白。
   窄屏退单列时 sticky 无意义,用媒体查询关掉。 */
.persona-col--static {
  position: sticky;
  top: 0;
  align-self: start;
}

@media (max-width: 980px) {
  .persona-col--static {
    position: static;
  }
}

/* 两栏顶部同一条基线:左边「我是谁」,右边「我在做什么」 */
.col-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--line);
}

.col-head--sub {
  margin-top: 26px;
}

.col-kicker {
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 15px;
  letter-spacing: 0.04em;
}

.col-meta {
  color: var(--ink-3);
  font-size: 12px;
}

/* ---------- 左栏:事实 ---------- */

.fact-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.fact-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: text;
  transition: background 0.12s ease;
}

.fact-row:hover,
.fact-row:focus-within {
  background: var(--bg-soft);
}

.fact-key {
  color: var(--ink-3);
  font-size: 13px;
}

.fact-input {
  width: 100%;
  padding: 3px 0;
  border: 0;
  border-bottom: 1px dashed transparent;
  color: var(--ink);
  font-family: inherit;
  font-size: 13.5px;
  background: transparent;
}

.fact-input:hover {
  border-bottom-color: var(--line-strong);
}

.fact-input:focus {
  border-bottom-color: var(--accent);
  outline: none;
}

.fact-select {
  cursor: pointer;
  appearance: none;
}

.charter-box {
  width: 100%;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 13px;
  line-height: 1.85;
  background: var(--panel);
  resize: vertical;
}

.charter-box:focus {
  border-color: var(--accent);
  outline: none;
}

/* 首次使用:问卷卡 */
.fact-card {
  padding: 20px 22px;
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
  color: var(--ink-2);
  font-size: 13.5px;
  background: var(--panel);
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
}

.chip:hover {
  border-color: var(--accent);
}

.chip--selected {
  border-color: var(--accent);
  color: var(--accent-ink);
  background: var(--accent-soft);
}

.textarea {
  width: 100%;
  font-family: inherit;
  resize: vertical;
}

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
}

.saved-hint {
  color: var(--ok);
  font-size: 13px;
}

.leave-link {
  margin-left: auto;
  color: var(--ink-3);
  font-size: 13.5px;
  cursor: pointer;
}

.leave-link:hover {
  color: var(--accent);
  text-decoration: underline;
}

/* ---------- 右栏:统计 ---------- */

.stat-block {
  margin-bottom: 26px;
}

.stat-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 10px;
  color: var(--ink-2);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.02em;
}

.stat-count {
  color: var(--ink-3);
  font-weight: 400;
}

.stat-empty {
  margin: 0;
  color: var(--ink-3);
  font-size: 12.5px;
}

/* 横向条:标签 / 轨道 / 数字 三段对齐,数字右对齐成一列 */
.bar-row {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.bar-item {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr) 34px;
  gap: 10px;
  align-items: center;
}

.bar-label {
  overflow: hidden;
  color: var(--ink-2);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-track {
  height: 7px;
  border-radius: 4px;
  background: var(--bg-soft);
  overflow: hidden;
}

.bar-fill {
  display: block;
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s ease;
}

.bar-fill--source {
  background: var(--accent);
}

.bar-fill--core {
  background: var(--ink);
}

.bar-fill--normal {
  background: var(--ink-3);
}

.bar-fill--reference {
  background: var(--line-strong);
}

.bar-fill--topic {
  background: var(--accent);
}

.bar-count {
  color: var(--ink-3);
  font-size: 12px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* 三段堆叠条:库内/联网/自答 */
.stack-bar {
  display: flex;
  height: 9px;
  gap: 2px;
  margin-bottom: 10px;
  border-radius: 5px;
  overflow: hidden;
}

.stack-seg {
  display: block;
  min-width: 2px;
}

.stack-seg--kb {
  background: var(--ink);
}

.stack-seg--web {
  background: var(--accent);
}

.stack-seg--self {
  background: var(--seal);
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
}

.legend-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.legend-dot--kb {
  background: var(--ink);
}

.legend-dot--web {
  background: var(--accent);
}

.legend-dot--self {
  background: var(--seal);
}

.legend-label {
  color: var(--ink-2);
}

.legend-pct {
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}

/* 榜单 */
.rank-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.rank-item {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: baseline;
  padding: 6px 0;
  border-bottom: 1px dashed var(--line);
}

.rank-item:last-child {
  border-bottom: 0;
}

.rank-no {
  color: var(--ink-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.rank-title {
  overflow: hidden;
  color: var(--ink);
  font-size: 13px;
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-title:hover {
  color: var(--accent);
  text-decoration: underline;
}

.rank-hits {
  color: var(--ink-3);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
}

/* 普通清单(死库存 / 自答问题) */
.plain-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.plain-item {
  padding: 5px 0;
  overflow: hidden;
  color: var(--ink-2);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plain-link {
  color: var(--ink-2);
  text-decoration: none;
}

.plain-link:hover {
  color: var(--accent);
  text-decoration: underline;
}

.plain-item--quote {
  color: var(--ink-3);
}

/* 每周柱状:柱高用像素(见 activityBars 注释),槽只负责横向分配与底部对齐 */
.week-chart {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 92px;
}

.week-col {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
  gap: 5px;
}

.week-slot {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  width: 100%;
}

.week-bar {
  width: 100%;
  max-width: 26px;
  border-radius: 3px 3px 0 0;
  background: var(--accent-soft);
  border-top: 2px solid var(--accent);
  transition: height 0.3s ease;
}

.week-label {
  color: var(--ink-3);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

/* 小号按钮(主题分析触发) */
.btn--tiny {
  padding: 3px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  color: var(--ink-2);
  font-size: 11.5px;
  background: var(--panel);
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn--tiny:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent-ink);
}

.btn--tiny:disabled {
  opacity: 0.55;
  cursor: default;
}
</style>
