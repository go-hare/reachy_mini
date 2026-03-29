import * as React from "react"
import { Bot, Code, Copy, FolderGit, History, MessageCircle } from "lucide-react"

import { RecentProject } from "@/hooks/use-recent-projects"
import { Button } from "@/components/ui/button"
import { ProjectActionsMenu, useProjectApplications } from "@/components/project-actions-menu"

interface ProjectIdentityHeaderProps {
  project: RecentProject
  homeDir?: string | null
  onCopyPath: () => void
  activeTab?: string
  onTabChange?: (tab: string) => void
}

const VIEW_TABS = [
  { value: "chat", icon: MessageCircle, label: "Chat" },
  { value: "code", icon: Code, label: "Code" },
  { value: "history", icon: History, label: "History" },
  { value: "robot", icon: Bot, label: "Robot Workbench" },
] as const

export function ProjectIdentityHeader({
  project,
  homeDir,
  onCopyPath,
  activeTab,
  onTabChange,
}: ProjectIdentityHeaderProps) {
  const { projectApplications, loadingProjectApplications } = useProjectApplications()

  const shortenedPath = React.useMemo(() => {
    if (!homeDir) return project.path
    return project.path.startsWith(homeDir) ? `~${project.path.slice(homeDir.length)}` : project.path
  }, [homeDir, project.path])

  return (
    <div
      className="flex min-w-0 flex-1 items-center gap-3"
      data-testid="project-identity-header"
    >
      <div className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden">
        <FolderGit className="size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 overflow-hidden">
          <div className="flex min-w-0 items-center gap-2">
            <h1 className="min-w-0 shrink truncate text-[15px] font-semibold tracking-[-0.01em] text-foreground">
              {project.name}
            </h1>
            {project.git_status === "dirty" ? (
              <span className="hidden sm:inline-flex shrink-0 items-center gap-1 text-[11px] font-medium text-[hsl(var(--warning))]">
                <span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--warning))]" />
                Uncommitted
              </span>
            ) : null}
          </div>
          <div className="mt-1 flex min-w-0 items-center gap-1.5 overflow-hidden text-[12px] text-muted-foreground">
            <span className="min-w-0 truncate font-mono">{shortenedPath}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-6 w-6 shrink-0 rounded-md no-drag text-muted-foreground hover:text-foreground"
              onClick={onCopyPath}
              aria-label="Copy project path"
              title="Copy project path"
            >
              <Copy className="size-3.5" />
            </Button>
          </div>
        </div>
      </div>
      {onTabChange && (
        <div className="flex items-center gap-0.5 shrink-0 no-drag">
          {VIEW_TABS.map(({ value, icon: Icon, label }) => (
            <Button
              key={value}
              type="button"
              variant="ghost"
              size="icon"
              className={`h-8 w-8 rounded-md ${
                activeTab === value
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => onTabChange(value)}
              aria-label={label}
              title={label}
            >
              <Icon className="size-4" />
            </Button>
          ))}
        </div>
      )}
      <div className="shrink-0">
        <ProjectActionsMenu
          project={project}
          projectApplications={projectApplications}
          loadingProjectApplications={loadingProjectApplications}
          showLaunchActions
          showCreateActions={false}
          showDeleteAction={false}
          onCopyPath={onCopyPath}
          triggerLabel={`Project actions for ${project.name}`}
        />
      </div>
    </div>
  )
}
