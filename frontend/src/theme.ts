import type { ThemeMode } from './types'

export const THEME_KEY = 'pt-theme'

/** 读取持久化主题；无记录或隐私模式不可用时回退深色。 */
export function readTheme(): ThemeMode {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* 隐私模式下 localStorage 可能不可用 */
  }
  return 'dark'
}

/** 主题只落在根元素上，CSS 变量负责其余部分。 */
export function applyTheme(mode: ThemeMode) {
  document.documentElement.setAttribute('data-theme', mode)
  document.documentElement.style.colorScheme = mode
  try {
    localStorage.setItem(THEME_KEY, mode)
  } catch {
    /* 隐私模式：主题仍在当前会话生效 */
  }
}
