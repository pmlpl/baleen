import { shallowRef } from 'vue'
import type { RouteLocationNormalizedGeneric } from 'vue-router'

/**
 * 上一条应用内路由(由 router/index.ts 的 afterEach 写入)。
 *
 * 给「返回」按钮做来源感知:从问答进来显示「返回对话」,从书架进来显示「返回书架」;
 * 直接刷新 / 外链打开时是 null,按钮退回兜底「回书架」。
 *
 * 单独成一个模块而不放进 router/index.ts:视图组件要读它,
 * 而 index.ts 又静态 import 全部视图,放那里会成循环依赖。
 * shallowRef:值整体替换,读它的 computed 才能跟着重算。
 */
export const prevRoute = shallowRef<RouteLocationNormalizedGeneric | null>(null)
