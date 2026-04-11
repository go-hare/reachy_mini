import type React from 'react'
import { useCallback, useMemo, useState } from 'react'
import { createCcminiSystemMessage } from '../ccmini/messageUtils.js'
import { getThemePickerIndex } from '../ccmini/overlayControlState.js'
import { saveConfiguredTheme } from '../ccmini/loadCcminiConfig.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'
import type { Message as MessageType } from '../types/message.js'

type UseCcminiThemeControllerOptions = {
  initialThemeSetting: ThemeSetting
  setMessages: React.Dispatch<React.SetStateAction<MessageType[]>>
}

export function useCcminiThemeController({
  initialThemeSetting,
  setMessages,
}: UseCcminiThemeControllerOptions): {
  activeThemeSetting: ThemeSetting
  themeSetting: ThemeSetting
  previewThemeSetting: ThemeSetting | null
  themePickerIndex: number
  syntaxHighlightingDisabled: boolean
  showThemePicker: boolean
  setPreviewThemeSetting: React.Dispatch<
    React.SetStateAction<ThemeSetting | null>
  >
  setThemePickerIndex: React.Dispatch<React.SetStateAction<number>>
  setSyntaxHighlightingDisabled: React.Dispatch<
    React.SetStateAction<boolean>
  >
  setShowThemePicker: React.Dispatch<React.SetStateAction<boolean>>
  openThemePicker: () => void
  closeThemePicker: () => void
  commitThemeSetting: (setting: ThemeSetting) => void
} {
  const [showThemePicker, setShowThemePicker] = useState(false)
  const [themeSetting, setThemeSetting] =
    useState<ThemeSetting>(initialThemeSetting)
  const [previewThemeSetting, setPreviewThemeSetting] =
    useState<ThemeSetting | null>(null)
  const [themePickerIndex, setThemePickerIndex] = useState(
    getThemePickerIndex(initialThemeSetting),
  )
  const [syntaxHighlightingDisabled, setSyntaxHighlightingDisabled] =
    useState(false)

  const openThemePicker = useCallback(() => {
    setThemePickerIndex(getThemePickerIndex(themeSetting))
    setPreviewThemeSetting(themeSetting)
    setShowThemePicker(true)
  }, [themeSetting])

  const closeThemePicker = useCallback(() => {
    setPreviewThemeSetting(null)
    setShowThemePicker(false)
  }, [])

  const commitThemeSetting = useCallback(
    (setting: ThemeSetting) => {
      setThemeSetting(setting)
      setPreviewThemeSetting(null)
      setShowThemePicker(false)
      try {
        saveConfiguredTheme(setting)
      } catch (error) {
        setMessages(prev => [
          ...prev,
          createCcminiSystemMessage(
            error instanceof Error
              ? error.message
              : 'Failed to save theme setting.',
            'error',
          ),
        ])
      }
    },
    [setMessages],
  )

  const activeThemeSetting = useMemo(
    () => previewThemeSetting ?? themeSetting,
    [previewThemeSetting, themeSetting],
  )

  return {
    activeThemeSetting,
    themeSetting,
    previewThemeSetting,
    themePickerIndex,
    syntaxHighlightingDisabled,
    showThemePicker,
    setPreviewThemeSetting,
    setThemePickerIndex,
    setSyntaxHighlightingDisabled,
    setShowThemePicker,
    openThemePicker,
    closeThemePicker,
    commitThemeSetting,
  }
}
