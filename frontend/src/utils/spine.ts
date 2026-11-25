/**
 * 书脊配色:按 note.id 稳定取色,同一本书每次进来颜色一致。
 * 国画颜料八色(压灰低饱和,水墨书卷气):墨/藏青/黛绿/赭石/朱砂/紫棠/苔绿/灰梅。
 * 书架书卡、列表行、纸篓行、飞纸动画共用——同一本书在哪儿都是那条颜色。
 */
const SPINES = [
  '#2f2c28', // 墨
  '#33506b', // 藏青
  '#3e5c50', // 黛绿
  '#8c5a3c', // 赭石
  '#a8432f', // 朱砂
  '#5d4a66', // 紫棠
  '#6b7a4d', // 苔绿
  '#75655e', // 灰梅
]

export function spineColor(id: number): string {
  return SPINES[id % SPINES.length] as string
}
