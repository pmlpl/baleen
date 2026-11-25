/**
 * 飞纸动画:从「书卡/行」或松手点的位置放飞一张小纸片,落进书立里的纸篓。
 * 纸片是临时 DOM 元素(document.body 下,样式在全局 styles.css 的 .fly-paper),
 * 走 transform 过渡,动画结束自动移除——Promise resolve 即「落篓」,
 * 调用方随后才真正把笔记记入纸篓(桌面回收站的「丢进去」手感)。
 */

/** 飞行源:元素或一个已量好的矩形(拖放的源元素在 dragstart 时已序列化坐标) */
export type FlySource =
  | HTMLElement
  | { left: number; top: number; width: number; height: number }
  | null

const FLY_MS = 550

/** 拖放链路里 dragstart 存的是裸对象,这里收口成可信矩形 */
function asRect(source: FlySource): { left: number; top: number; width: number; height: number } | null {
  if (source === null) return null
  if (source instanceof HTMLElement) return source.getBoundingClientRect()
  const { left, top, width, height } = source
  const nums = [left, top, width, height]
  if (nums.some((v) => typeof v !== 'number' || !Number.isFinite(v))) return null
  return { left, top, width, height }
}

export function flyToBasket(
  source: FlySource,
  spine = '',
  durationMs = FLY_MS,
): Promise<void> {
  const basket = document.getElementById('trash-basket')
  const rect = asRect(source)
  if (basket === null || rect === null) return Promise.resolve()

  const target = basket.getBoundingClientRect()
  const paper = document.createElement('div')
  paper.className = 'fly-paper'
  if (spine !== '') paper.style.setProperty('--spine', spine)
  // 起点:源元素中心;终点:纸篓中心
  const startX = rect.left + rect.width / 2 - 14
  const startY = rect.top + rect.height / 2 - 18
  const endX = target.left + target.width / 2 - 14
  const endY = target.top + target.height / 2 - 18
  paper.style.left = `${startX}px`
  paper.style.top = `${startY}px`
  document.body.appendChild(paper)

  // 强制回流,让起点先完成布局,再切到终态触发 transform 过渡
  void paper.offsetWidth
  paper.style.transitionDuration = `${durationMs}ms`
  paper.style.transform = `translate(${endX - startX}px, ${endY - startY}px) rotate(24deg) scale(0.22)`
  paper.style.opacity = '0.3'

  return new Promise((resolve) => {
    window.setTimeout(() => {
      paper.remove()
      resolve()
    }, durationMs + 40)
  })
}
