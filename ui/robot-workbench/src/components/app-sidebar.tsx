import * as React from "react"
import { invoke } from "@tauri-apps/api/core"
import {
  Clock,
  Folder,
  FolderGit,
  Home,
  MoreVertical,
  Plus,
  Settings,
} from "lucide-react"

import { ProjectActionsMenu } from "@/components/project-actions-menu"
import { SearchForm } from "@/components/search-form"
import { useRecentProjects, RecentProject } from "@/hooks/use-recent-projects"
import { ResizableSidebar } from "@/components/resizable-sidebar"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar"

interface AppSidebarProps extends React.ComponentProps<typeof Sidebar> {
  onRefreshProjects?: React.MutableRefObject<{ refresh: () => void } | null>
  onProjectSelect?: (project: RecentProject) => void
  currentProject?: RecentProject | null
  onHomeClick?: () => void
  onOpenSettings?: () => void
  onAddProjectClick?: () => void
  onProjectDeleted?: (projectPath: string) => void
  executingProjectPaths?: Set<string>
  onProjectBranchCreated?: (project: RecentProject, branch: string) => Promise<void> | void
  onProjectWorktreeCreated?: (project: RecentProject, worktreePath: string) => Promise<void> | void
  onDocSelect?: (slug: string) => void
}

export function AppSidebar({
  onRefreshProjects,
  onProjectSelect,
  currentProject,
  onHomeClick,
  onOpenSettings,
  onAddProjectClick,
  onProjectDeleted,
  executingProjectPaths,
  onProjectBranchCreated,
  onProjectWorktreeCreated,
  onDocSelect,
  ...props
}: AppSidebarProps) {
  const { projects, loading, error, refreshProjects } = useRecentProjects()

  React.useEffect(() => {
    if (onRefreshProjects) {
      onRefreshProjects.current = { refresh: refreshProjects }
    }
    return () => {
      if (onRefreshProjects) {
        onRefreshProjects.current = null
      }
    }
  }, [refreshProjects, onRefreshProjects])

  const handleDragStart = async (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest(".no-drag")) {
      return
    }

    try {
      await invoke("start_drag")
    } catch (error) {
      console.warn("Failed to start window drag:", error)
    }
  }

  const handleProjectDeletedFromMenu = React.useCallback(async (projectPath: string) => {
    await refreshProjects()
    onProjectDeleted?.(projectPath)
  }, [onProjectDeleted, refreshProjects])

  const handleBranchCreatedFromMenu = React.useCallback(async (project: RecentProject, branch: string) => {
    await onProjectBranchCreated?.(project, branch)
  }, [onProjectBranchCreated])

  const handleWorktreeCreatedFromMenu = React.useCallback(async (project: RecentProject, worktreePath: string) => {
    await onProjectWorktreeCreated?.(project, worktreePath)
  }, [onProjectWorktreeCreated])

  const handleOpenSettingsClick = React.useCallback((event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    event.stopPropagation()
    onOpenSettings?.()
  }, [onOpenSettings])

  return (
    <ResizableSidebar>
      <Sidebar variant="sidebar" className="flex flex-col" data-testid="app-sidebar" {...props}>
        <div
          className="h-2 w-full drag-area"
          data-tauri-drag-region
          onMouseDown={handleDragStart}
        />

        <SidebarHeader className="px-4">
          <SearchForm onDocSelect={onDocSelect} />
        </SidebarHeader>

        <SidebarContent className="flex-1">
          <SidebarGroup>
            <SidebarMenu className="mb-4">
              <SidebarMenuItem>
                <SidebarMenuButton asChild>
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault()
                      onHomeClick?.()
                    }}
                    className="flex items-center gap-2"
                  >
                    <Home className="size-4" />
                    <span>Home</span>
                  </a>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>

            <SidebarGroupLabel>Projects</SidebarGroupLabel>
            {onAddProjectClick ? (
              <SidebarGroupAction
                title="Add Project"
                onClick={onAddProjectClick}
                className="no-drag"
              >
                <Plus className="size-4" />
                <span className="sr-only">Add Project</span>
              </SidebarGroupAction>
            ) : null}

            <SidebarGroupContent>
              <SidebarMenu>
                {loading ? (
                  <SidebarMenuItem>
                    <SidebarMenuButton disabled>
                      <Clock className="size-4 animate-pulse" />
                      <span>Loading projects...</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ) : error ? (
                  <SidebarMenuItem>
                    <SidebarMenuButton disabled>
                      <Clock className="size-4 text-destructive" />
                      <span className="text-destructive text-sm">Failed to load projects</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ) : projects.length === 0 ? (
                  <SidebarMenuItem>
                    <SidebarMenuButton disabled>
                      <Folder className="size-4 text-muted-foreground" />
                      <span className="text-muted-foreground text-sm">No projects found</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ) : (
                  projects.map((project) => {
                    const isActive = currentProject?.path === project.path

                    return (
                      <SidebarMenuItem key={project.path}>
                        <SidebarMenuButton
                          asChild
                          isActive={isActive}
                        >
                          <a
                            href="#"
                            onClick={(e) => {
                              e.preventDefault()
                              onProjectSelect?.(project)
                            }}
                            title={project.path}
                          >
                            {project.is_git_repo ? (
                              <FolderGit className="size-4" />
                            ) : (
                              <Folder className="size-4" />
                            )}
                            <div className="flex items-center flex-1 min-w-0">
                              <span className="text-sm truncate w-full">{project.name}</span>
                            </div>
                            {executingProjectPaths?.has(project.path) ? (
                              <svg className="size-3.5 animate-spin shrink-0 ml-auto" viewBox="0 0 14 14" aria-label="Agent running">
                                <circle
                                  cx="7"
                                  cy="7"
                                  r="5.5"
                                  fill="none"
                                  strokeWidth="2"
                                  stroke="hsl(var(--sidebar-primary))"
                                  strokeOpacity="0.25"
                                />
                                <circle
                                  cx="7"
                                  cy="7"
                                  r="5.5"
                                  fill="none"
                                  strokeWidth="2"
                                  stroke="hsl(var(--sidebar-primary))"
                                  strokeDasharray="20 14"
                                  strokeLinecap="round"
                                />
                              </svg>
                            ) : null}
                            <ProjectActionsMenu
                              project={project}
                              onProjectDeleted={handleProjectDeletedFromMenu}
                              onProjectBranchCreated={handleBranchCreatedFromMenu}
                              onProjectWorktreeCreated={handleWorktreeCreatedFromMenu}
                              trigger={
                                <span
                                  role="button"
                                  tabIndex={0}
                                  className="ml-auto flex size-5 shrink-0 items-center justify-center rounded-md text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground no-drag"
                                  onClick={(e) => { e.preventDefault(); e.stopPropagation() }}
                                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation() } }}
                                  aria-label={`Project actions for ${project.name}`}
                                >
                                  <MoreVertical className="size-4" />
                                </span>
                              }
                            />
                          </a>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    )
                  })
                )}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarSeparator />
        <SidebarFooter className="px-2 pb-2 pt-0">
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                type="button"
                onClick={handleOpenSettingsClick}
                className="no-drag"
                aria-label="Open Settings"
              >
                <Settings className="size-4" />
                <span>Settings</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </ResizableSidebar>
  )
}
