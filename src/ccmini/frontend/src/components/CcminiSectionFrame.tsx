import * as React from 'react'
import { Box } from '../ink.js'
import { applyForeground } from '../ccmini/ansiText.js'
import {
  getResolvedThemeSetting,
  getThemeTokens,
} from '../ccmini/themePalette.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'

export function getFrameBorderColor(
  themeSetting: ThemeSetting,
): 'ansi:red' | 'ansi:redBright' {
  return getResolvedThemeSetting(themeSetting).startsWith('light')
    ? 'ansi:red'
    : 'ansi:redBright'
}

function PanelFrame({
  title,
  subtitle,
  themeSetting,
  children,
  titleColor,
}: {
  title: string
  subtitle?: string
  themeSetting: ThemeSetting
  children: React.ReactNode
  titleColor?: string
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={getFrameBorderColor(themeSetting)}
      borderText={{
        content: `${applyForeground(` ${title} `, titleColor ?? theme.claude)}${
          subtitle ? applyForeground(` ${subtitle} `, theme.subtle) : ''
        }`,
        position: 'top',
        align: 'start',
        offset: 1,
      }}
      paddingX={1}
      paddingY={0}
      width="100%"
    >
      {children}
    </Box>
  )
}

export function ConsoleSection({
  title,
  subtitle,
  themeSetting,
  children,
  titleColor,
  marginTop = 1,
}: {
  title: string
  subtitle?: string
  themeSetting: ThemeSetting
  children: React.ReactNode
  titleColor?: string
  marginTop?: number
}): React.ReactNode {
  return (
    <Box marginTop={marginTop}>
      <PanelFrame
        title={title}
        subtitle={subtitle}
        themeSetting={themeSetting}
        titleColor={titleColor}
      >
        <Box flexDirection="column" paddingX={1}>
          {children}
        </Box>
      </PanelFrame>
    </Box>
  )
}
