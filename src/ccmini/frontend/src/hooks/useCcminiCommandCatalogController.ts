import type React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getDonorCommandSuggestions,
  type DonorCommandCatalogEntry,
} from '../ccmini/donorCommandCatalog.js'
import {
  clampCommandCatalogIndex,
  getSelectedCatalogEntry,
} from '../ccmini/overlayControlState.js'
import { getCommandAutocompleteValue } from '../ccmini/donorCommandPresentation.js'
import { getDonorCommandQuery } from '../ccmini/replInputState.js'

type UseCcminiCommandCatalogControllerOptions = {
  trimmedInputValue: string
  applyMainInputState: (nextValue: string, nextOffset: number) => void
  setShowPromptHelp: React.Dispatch<React.SetStateAction<boolean>>
}

export function useCcminiCommandCatalogController({
  trimmedInputValue,
  applyMainInputState,
  setShowPromptHelp,
}: UseCcminiCommandCatalogControllerOptions): {
  showCommandCatalog: boolean
  commandCatalogIndex: number
  donorCommandQuery: string | null
  donorCommandSuggestions: DonorCommandCatalogEntry[]
  selectedDonorCommand: DonorCommandCatalogEntry | null
  setShowCommandCatalog: React.Dispatch<React.SetStateAction<boolean>>
  setCommandCatalogIndex: React.Dispatch<React.SetStateAction<number>>
  closeCommandCatalog: () => void
  autocompleteSelectedCommand: (
    entry: DonorCommandCatalogEntry | null,
  ) => void
} {
  const [showCommandCatalog, setShowCommandCatalog] = useState(false)
  const [commandCatalogIndex, setCommandCatalogIndex] = useState(0)
  const donorCommandQuery = useMemo(
    () => getDonorCommandQuery(trimmedInputValue, showCommandCatalog),
    [showCommandCatalog, trimmedInputValue],
  )
  const donorCommandSuggestions = useMemo(() => {
    if (donorCommandQuery === null) {
      return []
    }

    return getDonorCommandSuggestions(donorCommandQuery)
  }, [donorCommandQuery])

  useEffect(() => {
    setCommandCatalogIndex(prev =>
      clampCommandCatalogIndex(prev, donorCommandSuggestions.length),
    )
  }, [donorCommandSuggestions.length])

  const closeCommandCatalog = useCallback(() => {
    setShowCommandCatalog(false)
  }, [])

  const autocompleteSelectedCommand = useCallback(
    (entry: DonorCommandCatalogEntry | null): void => {
      if (!entry) {
        return
      }
      const nextValue = getCommandAutocompleteValue(entry)
      applyMainInputState(nextValue, nextValue.length)
      setShowCommandCatalog(false)
      setShowPromptHelp(false)
    },
    [applyMainInputState, setShowPromptHelp],
  )

  const selectedDonorCommand = useMemo(
    () => getSelectedCatalogEntry(donorCommandSuggestions, commandCatalogIndex),
    [commandCatalogIndex, donorCommandSuggestions],
  )

  return {
    showCommandCatalog,
    commandCatalogIndex,
    donorCommandQuery,
    donorCommandSuggestions,
    selectedDonorCommand,
    setShowCommandCatalog,
    setCommandCatalogIndex,
    closeCommandCatalog,
    autocompleteSelectedCommand,
  }
}
