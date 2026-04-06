import type { ThemeSetting } from './themeTypes.js'

type ThemeTokens = {
  claude: string
  permission: string
  text: string
  inverseText: string
  subtle: string
  warning: string
  error: string
  clawd_body: string
  clawd_background: string
  userMessageBackground: string
  messageActionsBackground: string
}

const lightTokens: ThemeTokens = {
  claude: 'rgb(215,119,87)',
  permission: 'rgb(87,105,247)',
  text: 'rgb(0,0,0)',
  inverseText: 'rgb(255,255,255)',
  subtle: 'rgb(175,175,175)',
  warning: 'rgb(150,108,30)',
  error: 'rgb(171,43,63)',
  clawd_body: 'rgb(215,119,87)',
  clawd_background: 'rgb(0,0,0)',
  userMessageBackground: 'rgb(240, 240, 240)',
  messageActionsBackground: 'rgb(232, 236, 244)',
}

const lightAnsiTokens: ThemeTokens = {
  claude: 'ansi:redBright',
  permission: 'ansi:blue',
  text: 'ansi:black',
  inverseText: 'ansi:white',
  subtle: 'ansi:blackBright',
  warning: 'ansi:yellow',
  error: 'ansi:red',
  clawd_body: 'ansi:redBright',
  clawd_background: 'ansi:black',
  userMessageBackground: 'ansi:white',
  messageActionsBackground: 'ansi:white',
}

const lightDaltonizedTokens: ThemeTokens = {
  claude: 'rgb(255,153,51)',
  permission: 'rgb(51,102,255)',
  text: 'rgb(0,0,0)',
  inverseText: 'rgb(255,255,255)',
  subtle: 'rgb(175,175,175)',
  warning: 'rgb(255,153,0)',
  error: 'rgb(204,0,0)',
  clawd_body: 'rgb(215,119,87)',
  clawd_background: 'rgb(0,0,0)',
  userMessageBackground: 'rgb(220, 220, 220)',
  messageActionsBackground: 'rgb(210, 216, 226)',
}

const darkTokens: ThemeTokens = {
  claude: 'rgb(215,119,87)',
  permission: 'rgb(177,185,249)',
  text: 'rgb(255,255,255)',
  inverseText: 'rgb(0,0,0)',
  subtle: 'rgb(80,80,80)',
  warning: 'rgb(255,193,7)',
  error: 'rgb(255,107,128)',
  clawd_body: 'rgb(215,119,87)',
  clawd_background: 'rgb(0,0,0)',
  userMessageBackground: 'rgb(55, 55, 55)',
  messageActionsBackground: 'rgb(44, 50, 62)',
}

const darkAnsiTokens: ThemeTokens = {
  claude: 'ansi:redBright',
  permission: 'ansi:blueBright',
  text: 'ansi:whiteBright',
  inverseText: 'ansi:black',
  subtle: 'ansi:white',
  warning: 'ansi:yellowBright',
  error: 'ansi:redBright',
  clawd_body: 'ansi:redBright',
  clawd_background: 'ansi:black',
  userMessageBackground: 'ansi:blackBright',
  messageActionsBackground: 'ansi:blackBright',
}

const darkDaltonizedTokens: ThemeTokens = {
  claude: 'rgb(255,153,51)',
  permission: 'rgb(153,204,255)',
  text: 'rgb(255,255,255)',
  inverseText: 'rgb(0,0,0)',
  subtle: 'rgb(80,80,80)',
  warning: 'rgb(255,204,0)',
  error: 'rgb(255,102,102)',
  clawd_body: 'rgb(215,119,87)',
  clawd_background: 'rgb(0,0,0)',
  userMessageBackground: 'rgb(55, 55, 55)',
  messageActionsBackground: 'rgb(44, 50, 62)',
}

export function getResolvedThemeSetting(
  setting: ThemeSetting,
): Exclude<ThemeSetting, 'auto'> {
  return setting === 'auto' ? getSystemThemeName() : setting
}

export function getThemeTokens(setting: ThemeSetting): ThemeTokens {
  switch (getResolvedThemeSetting(setting)) {
    case 'light':
      return lightTokens
    case 'light-ansi':
      return lightAnsiTokens
    case 'light-daltonized':
      return lightDaltonizedTokens
    case 'dark-ansi':
      return darkAnsiTokens
    case 'dark-daltonized':
      return darkDaltonizedTokens
    default:
      return darkTokens
  }
}

type SystemTheme = 'dark' | 'light'

let cachedSystemTheme: SystemTheme | undefined

function getSystemThemeName(): SystemTheme {
  if (cachedSystemTheme === undefined) {
    cachedSystemTheme = detectFromColorFgBg() ?? 'dark'
  }
  return cachedSystemTheme
}

function detectFromColorFgBg(): SystemTheme | undefined {
  const colorfgbg = process.env['COLORFGBG']
  if (!colorfgbg) {
    return undefined
  }

  const parts = colorfgbg.split(';')
  const bg = parts[parts.length - 1]
  if (bg === undefined || bg === '') {
    return undefined
  }

  const bgNum = Number(bg)
  if (!Number.isInteger(bgNum) || bgNum < 0 || bgNum > 15) {
    return undefined
  }

  return bgNum <= 6 || bgNum === 8 ? 'dark' : 'light'
}
