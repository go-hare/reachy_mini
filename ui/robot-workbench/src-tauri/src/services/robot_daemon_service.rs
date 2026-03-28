use chrono::Utc;
use serde::Serialize;
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read};
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::{Arc, Mutex};

const ROBOT_DAEMON_COMMAND: &str = "reachy-mini-daemon";
const ROBOT_DAEMON_ARGS: &[&str] = &["--sim"];
const ROBOT_DAEMON_LABEL: &str = "reachy-mini-daemon --sim";
const MAX_RECENT_LOG_LINES: usize = 80;

#[derive(Debug, Clone, Serialize)]
pub struct RobotDaemonProcessStatus {
    pub lifecycle: String,
    pub pid: Option<u32>,
    pub command: String,
    pub working_dir: Option<String>,
    pub started_at: Option<String>,
    pub exit_code: Option<i32>,
    pub last_error: Option<String>,
    pub recent_logs: Vec<String>,
}

#[derive(Clone)]
pub struct RobotDaemonManager {
    manager: ManagedProcessManager,
}

#[derive(Clone)]
pub struct MujocoViewerServiceManager {
    manager: ManagedProcessManager,
}

#[derive(Clone)]
struct ManagedProcessManager {
    inner: Arc<Mutex<ManagedProcessState>>,
}

enum ProcessStopStrategy {
    DirectChild,
    TreeOnWindows,
}

struct ManagedProcess {
    child: Child,
    pid: u32,
}

struct ManagedProcessState {
    process: Option<ManagedProcess>,
    last_started_at: Option<String>,
    last_working_dir: Option<String>,
    last_exit_code: Option<i32>,
    last_error: Option<String>,
    recent_logs: VecDeque<String>,
    default_command: String,
    last_command: String,
}

impl Default for RobotDaemonManager {
    fn default() -> Self {
        Self {
            manager: ManagedProcessManager::new(ROBOT_DAEMON_LABEL),
        }
    }
}

impl Default for MujocoViewerServiceManager {
    fn default() -> Self {
        Self {
            manager: ManagedProcessManager::new(""),
        }
    }
}

impl RobotDaemonManager {
    pub fn start_sim(
        &self,
        working_dir: Option<String>,
    ) -> Result<RobotDaemonProcessStatus, String> {
        let mut command = Command::new(ROBOT_DAEMON_COMMAND);
        command.args(ROBOT_DAEMON_ARGS);

        self.manager
            .start_command(working_dir, command, ROBOT_DAEMON_LABEL.to_string())
    }

    pub fn stop(&self) -> Result<RobotDaemonProcessStatus, String> {
        self.manager.stop(ProcessStopStrategy::DirectChild)
    }

    pub fn status(&self) -> Result<RobotDaemonProcessStatus, String> {
        self.manager.status()
    }
}

impl MujocoViewerServiceManager {
    pub fn start(
        &self,
        working_dir: Option<String>,
        launch_command: String,
    ) -> Result<RobotDaemonProcessStatus, String> {
        let normalized_command = normalize_launch_command(&launch_command).ok_or_else(|| {
            "MuJoCo viewer launch command is empty. Set it in Settings first.".to_string()
        })?;
        let command = build_shell_command(&normalized_command);

        self.manager
            .start_command(working_dir, command, normalized_command)
    }

    pub fn stop(&self) -> Result<RobotDaemonProcessStatus, String> {
        self.manager.stop(ProcessStopStrategy::TreeOnWindows)
    }

    pub fn status(&self) -> Result<RobotDaemonProcessStatus, String> {
        self.manager.status()
    }
}

impl ManagedProcessManager {
    fn new(default_command: &str) -> Self {
        Self {
            inner: Arc::new(Mutex::new(ManagedProcessState::new(default_command))),
        }
    }

    fn start_command(
        &self,
        working_dir: Option<String>,
        mut command: Command,
        command_label: String,
    ) -> Result<RobotDaemonProcessStatus, String> {
        let inner = self.inner.clone();
        let mut state = inner
            .lock()
            .map_err(|_| "Robot daemon state lock is poisoned".to_string())?;

        state.refresh_process();
        if state.process.is_some() {
            return Err(format!(
                "{} is already running in the desktop shell.",
                display_command_label(&state.command_label())
            ));
        }

        let resolved_working_dir = resolve_working_dir(working_dir)?;
        command.stdin(Stdio::null());
        command.stdout(Stdio::piped());
        command.stderr(Stdio::piped());

        if let Some(dir) = resolved_working_dir.as_deref() {
            command.current_dir(dir);
        }

        let mut child = command.spawn().map_err(|error| {
            state.last_command = command_label.clone();
            state.last_error = Some(format!(
                "Failed to start {}: {}",
                display_command_label(&command_label),
                error
            ));
            format!(
                "Failed to start {}: {}",
                display_command_label(&command_label),
                error
            )
        })?;

        let pid = child.id();

        if let Some(stdout) = child.stdout.take() {
            spawn_log_reader(inner.clone(), stdout, "stdout");
        }
        if let Some(stderr) = child.stderr.take() {
            spawn_log_reader(inner.clone(), stderr, "stderr");
        }

        state.process = Some(ManagedProcess { child, pid });
        state.last_started_at = Some(Utc::now().to_rfc3339());
        state.last_working_dir = resolved_working_dir;
        state.last_exit_code = None;
        state.last_error = None;
        state.last_command = command_label.clone();
        push_recent_log_locked(
            &mut state,
            format!(
                "Desktop launched {} (pid {}).",
                display_command_label(&command_label),
                pid
            ),
        );

        Ok(state.snapshot())
    }

    fn stop(&self, stop_strategy: ProcessStopStrategy) -> Result<RobotDaemonProcessStatus, String> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| "Robot daemon state lock is poisoned".to_string())?;

        state.refresh_process();

        let Some(mut process) = state.process.take() else {
            return Ok(state.snapshot());
        };

        let command_label = state.command_label();
        push_recent_log_locked(
            &mut state,
            format!(
                "Stopping desktop-managed {} (pid {}).",
                display_command_label(&command_label),
                process.pid
            ),
        );

        let exit_status = stop_managed_process(&mut process, &command_label, stop_strategy)?;

        state.last_exit_code = exit_status.code();
        state.last_error = None;
        push_recent_log_locked(
            &mut state,
            format!(
                "Desktop-managed {} stopped{}.",
                display_command_label(&command_label),
                format_exit_suffix(exit_status.code())
            ),
        );

        Ok(state.snapshot())
    }

    fn status(&self) -> Result<RobotDaemonProcessStatus, String> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| "Robot daemon state lock is poisoned".to_string())?;
        state.refresh_process();
        Ok(state.snapshot())
    }
}

impl ManagedProcessState {
    fn new(default_command: &str) -> Self {
        Self {
            process: None,
            last_started_at: None,
            last_working_dir: None,
            last_exit_code: None,
            last_error: None,
            recent_logs: VecDeque::new(),
            default_command: default_command.to_string(),
            last_command: default_command.to_string(),
        }
    }

    fn command_label(&self) -> String {
        if self.last_command.trim().is_empty() {
            self.default_command.clone()
        } else {
            self.last_command.clone()
        }
    }

    fn refresh_process(&mut self) {
        let command_label = self.command_label();
        let display_label = display_command_label(&command_label).to_string();
        let mut finished_process = None;

        if let Some(process) = self.process.as_mut() {
            match process.child.try_wait() {
                Ok(Some(status)) => {
                    finished_process = Some((process.pid, status.code(), status.success()));
                }
                Ok(None) => {}
                Err(error) => {
                    self.last_error = Some(format!(
                        "Failed to inspect desktop-managed {}: {}",
                        display_label, error
                    ));
                    push_recent_log_locked(
                        self,
                        format!("Status check failed for {}: {}", display_label, error),
                    );
                    self.process = None;
                }
            }
        }

        if let Some((pid, exit_code, success)) = finished_process {
            self.process = None;
            self.last_exit_code = exit_code;
            if success {
                self.last_error = None;
            } else {
                self.last_error = Some(match exit_code {
                    Some(code) => format!("{} exited with code {}.", display_label, code),
                    None => format!("{} exited unexpectedly.", display_label),
                });
            }

            push_recent_log_locked(
                self,
                format!(
                    "{} (pid {}) exited{}.",
                    display_label,
                    pid,
                    format_exit_suffix(exit_code)
                ),
            );
        }
    }

    fn snapshot(&self) -> RobotDaemonProcessStatus {
        RobotDaemonProcessStatus {
            lifecycle: if self.process.is_some() {
                "running".to_string()
            } else {
                "stopped".to_string()
            },
            pid: self.process.as_ref().map(|process| process.pid),
            command: self.command_label(),
            working_dir: self.last_working_dir.clone(),
            started_at: self.last_started_at.clone(),
            exit_code: self.last_exit_code,
            last_error: self.last_error.clone(),
            recent_logs: self.recent_logs.iter().cloned().collect(),
        }
    }
}

fn normalize_launch_command(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn build_shell_command(launch_command: &str) -> Command {
    if cfg!(target_os = "windows") {
        let mut command = Command::new("cmd");
        command.args(["/C", launch_command]);
        command
    } else {
        let mut command = Command::new("sh");
        command.args(["-lc", launch_command]);
        command
    }
}

fn stop_managed_process(
    process: &mut ManagedProcess,
    command_label: &str,
    stop_strategy: ProcessStopStrategy,
) -> Result<ExitStatus, String> {
    if cfg!(target_os = "windows") && matches!(stop_strategy, ProcessStopStrategy::TreeOnWindows) {
        terminate_process_tree(process.pid, command_label)?;
    } else {
        process.child.kill().map_err(|error| {
            format!(
                "Failed to stop {}: {}",
                display_command_label(command_label),
                error
            )
        })?;
    }

    process.child.wait().map_err(|error| {
        format!(
            "Failed to wait for {} to stop: {}",
            display_command_label(command_label),
            error
        )
    })
}

fn terminate_process_tree(pid: u32, command_label: &str) -> Result<(), String> {
    let pid_string = pid.to_string();
    let output = Command::new("taskkill")
        .args(["/T", "/F", "/PID", pid_string.as_str()])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| {
            format!(
                "Failed to stop {} tree via taskkill: {}",
                display_command_label(command_label),
                error
            )
        })?;

    if output.status.success() {
        return Ok(());
    }

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let details = if !stderr.is_empty() {
        stderr
    } else if !stdout.is_empty() {
        stdout
    } else {
        "taskkill returned a non-zero exit status".to_string()
    };

    Err(format!(
        "Failed to stop {} tree via taskkill: {}",
        display_command_label(command_label),
        details
    ))
}

fn display_command_label(command_label: &str) -> &str {
    if command_label.trim().is_empty() {
        "process"
    } else {
        command_label
    }
}

fn resolve_working_dir(working_dir: Option<String>) -> Result<Option<String>, String> {
    let normalized = working_dir
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .or_else(|| {
            std::env::current_dir()
                .ok()
                .map(|path| path.to_string_lossy().to_string())
        });

    if let Some(dir) = normalized.as_deref() {
        let path = Path::new(dir);
        if !path.exists() {
            return Err(format!("Working directory does not exist: {}", dir));
        }
        if !path.is_dir() {
            return Err(format!("Working directory is not a folder: {}", dir));
        }
    }

    Ok(normalized)
}

fn format_exit_suffix(exit_code: Option<i32>) -> String {
    match exit_code {
        Some(code) => format!(" with code {}", code),
        None => String::new(),
    }
}

fn push_recent_log_locked(state: &mut ManagedProcessState, line: String) {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return;
    }

    state.recent_logs.push_back(trimmed.to_string());
    while state.recent_logs.len() > MAX_RECENT_LOG_LINES {
        state.recent_logs.pop_front();
    }
}

fn spawn_log_reader<R>(inner: Arc<Mutex<ManagedProcessState>>, reader: R, source: &'static str)
where
    R: Read + Send + 'static,
{
    std::thread::spawn(move || {
        let reader = BufReader::new(reader);
        for line in reader.lines() {
            let Ok(line) = line else {
                break;
            };

            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }

            let Ok(mut state) = inner.lock() else {
                break;
            };

            push_recent_log_locked(&mut state, format!("[{}] {}", source, trimmed));
        }
    });
}
