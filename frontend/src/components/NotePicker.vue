<script setup lang="ts">
/**
 * 引用笔记选择器(/检索 唤起的弹层,QaView 拆出的受控展示组件,C2 低风险拆分)。
 *
 * 打开状态、候选笔记、加载中、筛选词全由父组件 props 传入,因为:
 * - 输入框是问答页共享的(还兼任命令词/草稿/历史回溯),Esc/Enter 的键盘路由留在父组件;
 * - 笔记检索(listNotes)是 API 调用,组件内零 API 调用、零 store 访问,也在父组件。
 * 本组件只渲染全屏遮罩 + 菜单 + 笔记列表,动作经 close/select 交回父组件;
 * 回填 chip、还原草稿、清输入由父组件完成(父页的 pickNote)。
 */

/** 候选笔记的最小形状(父组件把 listNotes 的条目原样传入,多余字段无妨) */
interface PickerNote {
  id: number
  title: string
  chunk_count: number
}

defineProps<{
  /** 选择器是否打开 */
  open: boolean
  /** 候选笔记(父组件按标题筛选后的检索结果) */
  notes: PickerNote[]
  /** 检索进行中(空态文案区分「还没结果」与「正在搜」) */
  loading: boolean
  /** 当前筛选词(输入框原文;空/非空决定空态文案) */
  query: string
}>()

const emit = defineEmits<{
  /** 请求关闭:点遮罩/点取消(Esc 在父组件,也落到同一动作) */
  close: []
  /** 点了某篇笔记:请求引用它(父组件回填 chip) */
  select: [note: { id: number; title: string }]
}>()
</script>

<template>
  <!-- /检索 的笔记选择器:输入文字按标题筛,点击引用;
       全屏遮罩兜底「点外部也能退出」,菜单本体盖在遮罩之上 -->
  <div v-if="open" class="picker-mask" aria-hidden="true" @click="emit('close')"></div>
  <div v-if="open" class="slash-menu card slash-menu--picker">
    <div class="picker-head">
      <span class="picker-title muted">选择要引用的笔记</span>
      <button class="np-cancel" type="button" @click="emit('close')">取消</button>
    </div>
    <button
      v-for="n in notes"
      :key="n.id"
      class="slash-item"
      type="button"
      @click="emit('select', { id: n.id, title: n.title })"
    >
      <span class="slash-name">{{ n.title }}</span>
      <span class="slash-desc">{{ n.chunk_count }} 段 · 引用到提问</span>
    </button>
    <p v-if="notes.length === 0 && !loading" class="muted slash-empty">
      {{ query.trim() === '' ? '输入标题关键词筛选' : '没有匹配的笔记' }}
    </p>
  </div>
</template>

<style scoped>
/* 从 QaView 原样搬来;SlashMenu 的命令菜单用同名类,样式同源(那边有自己的副本) */
.slash-menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  right: 0;
  z-index: 30;
  max-height: 280px;
  padding: 6px;
  overflow-y: auto;
  box-shadow: var(--shadow);
}

/* 全屏遮罩兜底「点外部退出」,菜单本体盖在遮罩上 */
.picker-mask {
  position: fixed;
  inset: 0;
  z-index: 29;
  background: rgba(20, 24, 33, 0.18);
}

.slash-menu--picker {
  z-index: 30;
}

.picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px 6px;
}

.picker-title {
  font-size: 12.5px;
}

/* 取消按钮:与 QaView 存笔记选项卡的 .np-cancel 同名同源,这边带一份自己的副本 */
.np-cancel {
  margin-top: 10px;
  border: none;
  background: none;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 12.5px;
}

.np-cancel:hover:not(:disabled) {
  color: var(--accent);
}

.picker-head .np-cancel {
  margin-top: 0;
}

.slash-item {
  display: flex;
  width: 100%;
  align-items: baseline;
  gap: 10px;
  padding: 8px 10px;
  border: none;
  border-radius: 7px;
  background: none;
  cursor: pointer;
  text-align: left;
}

.slash-name {
  color: var(--accent-ink);
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.slash-desc {
  overflow: hidden;
  color: var(--ink-2);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.slash-empty {
  padding: 10px;
  font-size: 12.5px;
}
</style>
