import { useSyncExternalStore } from 'react'
import type {
  CcminiBackgroundTask,
  CcminiConnectConfig,
  CcminiTaskBoardTask,
  CcminiTeamState,
} from './bridgeTypes.js'

export type PlanPanelState =
  | {
      mode: 'idle'
    }
  | {
      mode: 'active'
      detail: string
    }
  | {
      mode: 'ready'
      detail: string
      planLines: string[]
    }

export type CcminiTasksV2Snapshot = {
  tasks: CcminiTaskBoardTask[]
  backgroundTasks: CcminiBackgroundTask[]
  team: CcminiTeamState
  hidden: boolean
  error: string | null
  planState: PlanPanelState
}

const HIDE_DELAY_MS = 5000
const ACTIVE_POLL_MS = 1000
const IDLE_POLL_MS = 4000
const REFRESH_DEBOUNCE_MS = 50

const EMPTY_TEAM: CcminiTeamState = {
  name: '',
  members: [],
}

const EMPTY_SNAPSHOT: CcminiTasksV2Snapshot = {
  tasks: [],
  backgroundTasks: [],
  team: EMPTY_TEAM,
  hidden: false,
  error: null,
  planState: { mode: 'idle' },
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : null
}

function normalizePlannerTaskStatus(
  value: unknown,
): CcminiTaskBoardTask['status'] {
  if (
    value === 'completed' ||
    value === 'in_progress' ||
    value === 'pending'
  ) {
    return value
  }
  return 'pending'
}

function normalizeBackgroundTaskStatus(
  value: unknown,
): CcminiBackgroundTask['status'] {
  if (
    value === 'pending' ||
    value === 'running' ||
    value === 'completed' ||
    value === 'failed' ||
    value === 'killed'
  ) {
    return value
  }
  return 'running'
}

function parseRuntimePlanPanelState(value: unknown): PlanPanelState {
  const record = asRecord(value)
  if (!record) {
    return { mode: 'idle' }
  }

  const isActive = Boolean(record.isActive)
  const planText =
    typeof record.planText === 'string' ? record.planText.trim() : ''

  if (isActive) {
    return {
      mode: 'active',
      detail:
        'Read-only exploration is active. The agent is expected to inspect the codebase and propose a plan before editing.',
    }
  }

  if (planText) {
    const planLines = planText
      .split(/\r?\n/)
      .map(line => line.trim())
      .filter(Boolean)
      .filter(line => !line.startsWith('## '))
      .slice(0, 12)
    return {
      mode: 'ready',
      detail: 'A structured implementation plan is ready.',
      planLines,
    }
  }

  return { mode: 'idle' }
}

function snapshotsEqual(
  left: CcminiTasksV2Snapshot,
  right: CcminiTasksV2Snapshot,
): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function safeUnref(timer: ReturnType<typeof setTimeout> | null): void {
  if (timer && typeof (timer as { unref?: () => void }).unref === 'function') {
    ;(timer as { unref: () => void }).unref()
  }
}

class CcminiTasksStore {
  #listeners = new Set<() => void>()
  #subscriberCount = 0
  #started = false
  #snapshot: CcminiTasksV2Snapshot = EMPTY_SNAPSHOT
  #pollTimer: ReturnType<typeof setTimeout> | null = null
  #hideTimer: ReturnType<typeof setTimeout> | null = null
  #refreshTimer: ReturnType<typeof setTimeout> | null = null
  #fetchPromise: Promise<void> | null = null

  constructor(
    private readonly config: Pick<
      CcminiConnectConfig,
      'baseUrl' | 'authToken' | 'sessionId'
    >,
  ) {}

  getSnapshot = (): CcminiTasksV2Snapshot => this.#snapshot

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    this.#subscriberCount += 1
    if (!this.#started) {
      this.#started = true
      this.requestRefresh(0)
    }

    let unsubscribed = false
    return () => {
      if (unsubscribed) {
        return
      }
      unsubscribed = true
      this.#listeners.delete(listener)
      this.#subscriberCount -= 1
      if (this.#subscriberCount === 0) {
        this.#stop()
      }
    }
  }

  requestRefresh(delayMs = REFRESH_DEBOUNCE_MS): void {
    if (!this.#started) {
      return
    }
    if (this.#refreshTimer) {
      clearTimeout(this.#refreshTimer)
    }
    this.#refreshTimer = setTimeout(() => {
      this.#refreshTimer = null
      void this.#poll()
    }, Math.max(0, delayMs))
    safeUnref(this.#refreshTimer)
  }

  #notify(): void {
    for (const listener of this.#listeners) {
      listener()
    }
  }

  #setSnapshot(nextSnapshot: CcminiTasksV2Snapshot): void {
    if (snapshotsEqual(this.#snapshot, nextSnapshot)) {
      return
    }
    this.#snapshot = nextSnapshot
    this.#notify()
  }

  #schedulePoll(delayMs: number): void {
    if (this.#pollTimer) {
      clearTimeout(this.#pollTimer)
    }
    this.#pollTimer = setTimeout(() => {
      this.#pollTimer = null
      void this.#poll()
    }, delayMs)
    safeUnref(this.#pollTimer)
  }

  #clearHideTimer(): void {
    if (this.#hideTimer) {
      clearTimeout(this.#hideTimer)
      this.#hideTimer = null
    }
  }

  #scheduleHide(): void {
    this.#clearHideTimer()
    this.#hideTimer = setTimeout(() => {
      this.#hideTimer = null
      void this.#resetIfCompleted()
    }, HIDE_DELAY_MS)
    safeUnref(this.#hideTimer)
  }

  async #resetIfCompleted(): Promise<void> {
    const root = this.config.baseUrl.replace(/\/$/, '')
    try {
      const response = await fetch(`${root}/api/tasks/control`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${this.config.authToken}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          session_id: this.config.sessionId,
          action: 'reset_task_list_if_completed',
        }),
      })
      if (!response.ok) {
        this.#schedulePoll(IDLE_POLL_MS)
        return
      }
      const payload = (await response.json()) as {
        ok?: boolean
        cleared?: boolean
      }
      if (payload.ok && payload.cleared) {
        this.#setSnapshot({
          ...this.#snapshot,
          tasks: [],
          backgroundTasks: [],
          hidden: true,
          error: null,
        })
        this.#schedulePoll(IDLE_POLL_MS)
        return
      }
    } catch {
      // Ignore TTL reset failures; polling will reconcile state.
    }
    this.#schedulePoll(IDLE_POLL_MS)
  }

  async #poll(): Promise<void> {
    if (this.#fetchPromise) {
      return this.#fetchPromise
    }

    this.#fetchPromise = (async () => {
      const root = this.config.baseUrl.replace(/\/$/, '')
      const encodedSessionId = encodeURIComponent(this.config.sessionId)

      try {
        const response = await fetch(
          `${root}/api/tasks?session_id=${encodedSessionId}&include_completed=true`,
          {
            headers: { Authorization: `Bearer ${this.config.authToken}` },
          },
        )
        if (!response.ok) {
          this.#setSnapshot({
            ...this.#snapshot,
            error: `HTTP ${response.status}`,
          })
          this.#schedulePoll(IDLE_POLL_MS)
          return
        }

        const payload = (await response.json()) as {
          tasks?: unknown[]
          backgroundTasks?: unknown[]
          team?: unknown
          planState?: unknown
        }

        const nextTasks = Array.isArray(payload.tasks)
          ? payload.tasks
              .map(raw => asRecord(raw))
              .filter((record): record is Record<string, unknown> => record !== null)
              .map(record => ({
                id: typeof record.id === 'string' ? record.id : '',
                subject:
                  typeof record.subject === 'string' ? record.subject : '',
                description:
                  typeof record.description === 'string'
                    ? record.description
                    : '',
                activeForm:
                  typeof record.activeForm === 'string'
                    ? record.activeForm
                    : undefined,
                owner:
                  typeof record.owner === 'string' ? record.owner : undefined,
                ownerIsActive:
                  typeof record.ownerIsActive === 'boolean'
                    ? record.ownerIsActive
                    : undefined,
                status: normalizePlannerTaskStatus(record.status),
                blocks: Array.isArray(record.blocks)
                  ? record.blocks.filter(
                      (value): value is string => typeof value === 'string',
                    )
                  : [],
                blockedBy: Array.isArray(record.blockedBy)
                  ? record.blockedBy.filter(
                      (value): value is string => typeof value === 'string',
                    )
                  : [],
                metadata: asRecord(record.metadata) ?? undefined,
              }))
              .filter(task => task.id && task.subject)
          : []

        const nextBackgroundTasks = Array.isArray(payload.backgroundTasks)
          ? payload.backgroundTasks
              .map(raw => asRecord(raw))
              .filter((record): record is Record<string, unknown> => record !== null)
              .map(record => ({
                id: typeof record.id === 'string' ? record.id : '',
                type:
                  typeof record.type === 'string'
                    ? record.type
                    : 'local_agent',
                status: normalizeBackgroundTaskStatus(record.status),
                description:
                  typeof record.description === 'string'
                    ? record.description
                    : '',
                outputFile:
                  typeof record.outputFile === 'string'
                    ? record.outputFile
                    : undefined,
                transcriptFile:
                  typeof record.transcriptFile === 'string'
                    ? record.transcriptFile
                    : undefined,
                startTime:
                  typeof record.startTime === 'number'
                    ? record.startTime
                    : undefined,
                endTime:
                  typeof record.endTime === 'number'
                    ? record.endTime
                    : null,
                updatedAt:
                  typeof record.updatedAt === 'number'
                    ? record.updatedAt
                    : undefined,
                resumeCount:
                  typeof record.resumeCount === 'number'
                    ? record.resumeCount
                    : 0,
                canResume:
                  typeof record.canResume === 'boolean'
                    ? record.canResume
                    : false,
                promptPreview:
                  typeof record.promptPreview === 'string'
                    ? record.promptPreview
                    : undefined,
                model:
                  typeof record.model === 'string' ? record.model : undefined,
                profile:
                  typeof record.profile === 'string'
                    ? record.profile
                    : undefined,
                workerName:
                  typeof record.workerName === 'string'
                    ? record.workerName
                    : undefined,
                teamName:
                  typeof record.teamName === 'string'
                    ? record.teamName
                    : undefined,
                backendType:
                  typeof record.backendType === 'string'
                    ? record.backendType
                    : undefined,
                isolation:
                  typeof record.isolation === 'string'
                    ? record.isolation
                    : undefined,
                agentType:
                  typeof record.agentType === 'string'
                    ? record.agentType
                    : undefined,
                subagentType:
                  typeof record.subagentType === 'string'
                    ? record.subagentType
                    : undefined,
                result:
                  typeof record.result === 'string' ? record.result : undefined,
                error:
                  typeof record.error === 'string' ? record.error : undefined,
                metadata: asRecord(record.metadata) ?? undefined,
              }))
              .filter(task => task.id.length > 0)
          : []

        const teamRecord = asRecord(payload.team)
        const nextTeam: CcminiTeamState = {
          name:
            typeof teamRecord?.name === 'string' ? teamRecord.name : '',
          description:
            typeof teamRecord?.description === 'string'
              ? teamRecord.description
              : undefined,
          leadAgentId:
            typeof teamRecord?.leadAgentId === 'string'
              ? teamRecord.leadAgentId
              : undefined,
          leadSessionId:
            typeof teamRecord?.leadSessionId === 'string'
              ? teamRecord.leadSessionId
              : undefined,
          activeCount:
            typeof teamRecord?.activeCount === 'number'
              ? teamRecord.activeCount
              : undefined,
          teammateCount:
            typeof teamRecord?.teammateCount === 'number'
              ? teamRecord.teammateCount
              : undefined,
          members: Array.isArray(teamRecord?.members)
            ? teamRecord.members
                .map(raw => asRecord(raw))
                .filter((record): record is Record<string, unknown> => record !== null)
                .map(record => ({
                  agentId:
                    typeof record.agentId === 'string' ? record.agentId : '',
                  name: typeof record.name === 'string' ? record.name : '',
                  agentType:
                    typeof record.agentType === 'string'
                      ? record.agentType
                      : undefined,
                  model:
                    typeof record.model === 'string'
                      ? record.model
                      : undefined,
                  cwd:
                    typeof record.cwd === 'string' ? record.cwd : undefined,
                  status:
                    typeof record.status === 'string'
                      ? record.status
                      : undefined,
                  currentTask:
                    typeof record.currentTask === 'string'
                      ? record.currentTask
                      : undefined,
                  messagesProcessed:
                    typeof record.messagesProcessed === 'number'
                      ? record.messagesProcessed
                      : undefined,
                  totalTurns:
                    typeof record.totalTurns === 'number'
                      ? record.totalTurns
                      : undefined,
                  error:
                    typeof record.error === 'string'
                      ? record.error
                      : undefined,
                  isIdle:
                    typeof record.isIdle === 'boolean'
                      ? record.isIdle
                      : undefined,
                  isActive:
                    typeof record.isActive === 'boolean'
                      ? record.isActive
                      : undefined,
                  backendType:
                    typeof record.backendType === 'string'
                      ? record.backendType
                      : undefined,
                  transcriptFile:
                    typeof record.transcriptFile === 'string'
                      ? record.transcriptFile
                      : undefined,
                  color:
                    typeof record.color === 'string'
                      ? record.color
                      : undefined,
                  planModeRequired:
                    typeof record.planModeRequired === 'boolean'
                      ? record.planModeRequired
                      : undefined,
                  lastUpdateMs:
                    typeof record.lastUpdateMs === 'number'
                      ? record.lastUpdateMs
                      : undefined,
                }))
                .filter(member => member.agentId && member.name)
            : [],
        }

        const hasIncomplete =
          nextTasks.some(task => task.status !== 'completed') ||
          nextBackgroundTasks.some(
            task => task.status === 'running' || task.status === 'pending',
          ) ||
          nextTeam.members.some(
            member => member.isActive && member.status !== 'shutdown',
          )

        if (hasIncomplete || nextTasks.length === 0) {
          this.#clearHideTimer()
        } else if (this.#hideTimer === null) {
          this.#scheduleHide()
        }

        this.#setSnapshot({
          tasks:
            this.#snapshot.hidden && !hasIncomplete && nextTasks.length === 0
              ? []
              : nextTasks,
          backgroundTasks:
            this.#snapshot.hidden && !hasIncomplete && nextTasks.length === 0
              ? []
              : nextBackgroundTasks,
          team: nextTeam,
          hidden: this.#snapshot.hidden && !hasIncomplete && nextTasks.length === 0,
          error: null,
          planState: parseRuntimePlanPanelState(payload.planState),
        })

        this.#schedulePoll(hasIncomplete ? ACTIVE_POLL_MS : IDLE_POLL_MS)
      } catch (error) {
        this.#setSnapshot({
          ...this.#snapshot,
          error:
            error instanceof Error ? error.message : 'tasks fetch failed',
        })
        this.#schedulePoll(IDLE_POLL_MS)
      }
    })().finally(() => {
      this.#fetchPromise = null
    })

    return this.#fetchPromise
  }

  #stop(): void {
    if (this.#pollTimer) {
      clearTimeout(this.#pollTimer)
      this.#pollTimer = null
    }
    if (this.#refreshTimer) {
      clearTimeout(this.#refreshTimer)
      this.#refreshTimer = null
    }
    this.#clearHideTimer()
    this.#started = false
  }
}

const stores = new Map<string, CcminiTasksStore>()

function getStoreKey(
  config: Pick<CcminiConnectConfig, 'baseUrl' | 'authToken' | 'sessionId'>,
): string {
  return JSON.stringify([config.baseUrl, config.authToken, config.sessionId])
}

function getStore(
  config: Pick<CcminiConnectConfig, 'baseUrl' | 'authToken' | 'sessionId'>,
): CcminiTasksStore {
  const key = getStoreKey(config)
  let store = stores.get(key)
  if (!store) {
    store = new CcminiTasksStore(config)
    stores.set(key, store)
  }
  return store
}

export function useCcminiTasksV2Store(
  baseUrl: string,
  authToken: string,
  sessionId: string,
): CcminiTasksV2Snapshot {
  const store = getStore({ baseUrl, authToken, sessionId })
  return useSyncExternalStore(store.subscribe, store.getSnapshot)
}

export function requestCcminiTasksStoreRefresh(
  config: Pick<CcminiConnectConfig, 'baseUrl' | 'authToken' | 'sessionId'>,
  delayMs = REFRESH_DEBOUNCE_MS,
): void {
  const store = stores.get(getStoreKey(config))
  if (!store) {
    return
  }
  store.requestRefresh(delayMs)
}
