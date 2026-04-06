export type Tool = {
  name: string
  mcpInfo?: {
    serverName: string
    toolName: string
  }
}

export type Tools = readonly Tool[]

export type ToolPermissionContext = Record<string, unknown>
