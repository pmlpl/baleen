/**
 * 数学公式渲染(KaTeX):行内 $...$ 与块级 $$...$$。
 * 输入必须是未 HTML 转义的原文(KaTeX 要原生反斜杠,\frac 而非 &frac;);
 * 输出是 KaTeX 自产的安全 HTML(默认 trust 关闭,不执行外部内容),可直接拼进 v-html。
 * 渲染失败退化为原文文本 —— 公式坏了不能拖垮整篇笔记。
 */
import katex from 'katex'

/** 一个被识别出的数学片段 */
export interface MathSpan {
  tex: string
  display: boolean
}

/** 退化时也要过一遍转义(tex 是原文,可能含 < > &) */
function escapeTex(raw: string): string {
  return raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function renderTex(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex, {
      displayMode,
      throwOnError: false,
      strict: false,
    })
  } catch {
    return displayMode ? `<div>${escapeTex(tex)}</div>` : escapeTex(tex)
  }
}

/**
 * 在原文上扫描数学片段,切成 [文本 | MathSpan] 序列,由调用方做占位保护
 * (占位之后才能跑 HTML 转义/粗体斜体/引用徽章,公式里的 [1] ** | 不会被行内替换吃掉)。
 *
 * 启发式(与 pandoc/auto-render 同款):
 * - 开定界符右侧不贴空白、片段内不换行、内容非空;
 * - 行内闭合 $ 左侧不贴空白,右侧不紧跟数字(挡 $10,000 and $20,000 这类美元价签);
 * - 被拒的闭合候选位不再复用为开定界符(否则 $100 and $200, or 5$ 会把中段配成公式)。
 * 不满足就当普通文字;未配对的 $ 原样保留。
 */
export function splitMath(raw: string): Array<string | MathSpan> {
  const out: Array<string | MathSpan> = []
  let buf = ''
  let i = 0
  // 曾被拒为「闭合候选」的 $ 下标:主循环走到这里时只当字面量,不再尝试当开头
  let literalAt = -1

  const flush = (): void => {
    if (buf !== '') {
      out.push(buf)
      buf = ''
    }
  }

  while (i < raw.length) {
    if (i === literalAt) {
      literalAt = -1
      buf += raw[i]
      i += 1
      continue
    }
    const isDouble = raw[i] === '$' && raw[i + 1] === '$'
    const isSingle = !isDouble && raw[i] === '$'
    if (!isDouble && !isSingle) {
      buf += raw[i]
      i += 1
      continue
    }

    const closeLen = isDouble ? 2 : 1
    const start = i + closeLen
    // 开定界符内侧必须紧贴内容(不贴空白),否则按普通文字
    if (start >= raw.length || /\s/.test(raw[start] as string)) {
      buf += '$'.repeat(closeLen)
      i += closeLen
      continue
    }

    // 找闭合定界符
    let end = -1
    if (isDouble) {
      const j = raw.indexOf('$$', start)
      if (j > start) end = j
    } else {
      // 行内:片段里不许有换行;闭合 $ 前不贴空白、后不紧跟数字
      let j = start
      while (j < raw.length && raw[j] !== '$' && raw[j] !== '\n') j += 1
      if (raw[j] === '$' && j > start && !/\s/.test(raw[j - 1] as string) && !/\d/.test(raw[j + 1] ?? '')) {
        end = j
      } else if (raw[j] === '$') {
        literalAt = j // 该 $ 也不再当开头
      }
    }
    if (end < 0) {
      buf += '$'.repeat(closeLen)
      i += closeLen
      continue
    }

    flush()
    out.push({ tex: raw.slice(start, end), display: isDouble })
    i = end + closeLen
  }
  flush()
  return out
}
