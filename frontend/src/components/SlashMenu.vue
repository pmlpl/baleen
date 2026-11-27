<script lang="ts">
/**
 * 菜单项的最小形状:菜单只渲染名字与描述;过滤(含关键词匹配)在父组件完成,
 * 所以 keywords/run 不进 props 契约,调用方自己的命令类型满足此形状即可。
 */
export interface SlashMenuItem {
  name: string
  desc: string
}
</script>

<script setup lang="ts" generic="C extends SlashMenuItem">
/**
 * 斜杠命令菜单(QaView 拆出的受控展示组件,C2 低风险拆分)。
 *
 * 问答页的输入框是共享的(命令词/笔记筛选/草稿/历史回溯都走它),所以:
 * - 菜单开关、命令过滤、键盘事件(方向键/Enter/Tab/Esc)的路由留在父组件;
 * - 本组件只负责渲染命令列表、鼠标悬停亮项、亮项滚进可视区、点击上报。
 * 数据与动作全由父组件传入(props 进/emit 出),组件内零 API 调用、零 store 访问;
 * 泛型 C 保留调用方完整的命令类型,选择事件原样回传。
 */
import { nextTick, ref, watch } from 'vue'

const props = defineProps<{
  /** 菜单是否打开(父组件由输入是否以 / 开头决定) */
  open: boolean
  /** 当前要显示的命令(父组件按输入过滤后的列表) */
  commands: C[]
  /** 亮项下标(键盘与鼠标共用一个) */
  active: number
}>()

const emit = defineEmits<{
  /** 选中某条命令(鼠标点击;键盘 Enter/Tab 在父组件处理后也落到同一动作) */
  select: [cmd: C]
  /** 鼠标悬停到第 index 项:请把亮项移过去 */
  hover: [index: number]
}>()

/** 菜单本体(亮项滚动跟随用) */
const slashMenuEl = ref<HTMLElement | null>(null)

// 高亮项跟着滚进可视区(命令多时菜单内部会滚动)
watch(
  () => props.active,
  () => {
    void nextTick(() => {
      slashMenuEl.value
        ?.querySelector('.slash-item--active')
        ?.scrollIntoView({ block: 'nearest' })
    })
  },
)
</script>

<template>
  <!-- / 命令菜单:绝对定位在输入框上方(定位锚是父页 .chat-input--anchor 的表单) -->
  <div v-if="open" ref="slashMenuEl" class="slash-menu card">
    <button
      v-for="(c, i) in commands"
      :key="c.name"
      class="slash-item"
      :class="{ 'slash-item--active': i === active }"
      type="button"
      @click="emit('select', c)"
      @mouseenter="emit('hover', i)"
    >
      <span class="slash-name mono">{{ c.name }}</span>
      <span class="slash-desc">{{ c.desc }}</span>
    </button>
    <p v-if="commands.length === 0" class="muted slash-empty">没有匹配的命令(输入 / 看全部)</p>
  </div>
</template>

<!-- 弹层样式在全局 styles.css(挂账③收口:与 NotePicker 的同名类共用一份,不再各带副本) -->
