/**
 * 插图按原始宽高比分类（方案 C，2026-11-19 主人拍板）。
 *
 * 背景：视频帧多为 16:9 全画面（该占整行），而课件竖版/手机截图/方图更适合
 * 缩到左侧小图、文字环绕——按「尺寸」一刀切都不对，得看**宽高比**。
 *
 * 判定：w/h <= 4/3 视为窄图（竖图/方图/接近方图）→ 左 float，文字环绕；
 * 更宽的（16:9、21:9 的屏幕截图）保持居中限宽。阈值 4/3 对齐常见课件比例，
 * 3:2 照片(1.5)仍算宽图——照片类本来就要看清细节。
 *
 * 为什么要等 onload：宽高比只能从 naturalWidth/naturalHeight 拿，doc 里
 * 只存 media key 不存尺寸（不想为排版在数据层加字段）。图片是本地同源
 * `/api/media/...`，load 很快；分类完成前先按现状（居中限宽）显示，
 * 不会出现「空白等图」。
 */

/** 宽高比阈值：w/h <= 4/3 → 窄图 */
const NARROW_MAX_RATIO = 4 / 3

export function applyFigureLayout(root: HTMLElement): void {
  const images = root.querySelectorAll<HTMLImageElement>('.note-img img')
  images.forEach((img) => {
    const figure = img.closest<HTMLElement>('.note-img')
    if (figure === null) return

    const classify = (): void => {
      const w = img.naturalWidth
      const h = img.naturalHeight
      if (w === 0 || h === 0) return
      const narrow = w / h <= NARROW_MAX_RATIO
      figure.classList.toggle('is-narrow', narrow)
      figure.classList.toggle('is-wide', !narrow)
    }

    if (img.complete && img.naturalWidth > 0) {
      classify() // 已在缓存里(切回笔记/浏览器缓存):立即分类,不闪
    } else {
      img.addEventListener('load', classify, { once: true })
      // 加载失败(图被删/网络断)就不分类,保持现状居中——alt 文字仍可读
    }
  })
}
