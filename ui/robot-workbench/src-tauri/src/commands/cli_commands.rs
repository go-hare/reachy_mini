use once_cell::sync::Lazy;
use std::collections::HashMap;
use std::path::Path;
use std::process::Command as StdCommand;
use tauri::Emitter;
use tokio::sync::Mutex;

use crate::models::*;

const SESSION_TIMEOUT_SECONDS: i64 = 1800; // 30 minutes

static SESSIONS: Lazy<Mutex<HashMap<String, CLISession>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

fn now_timestamp() -> i64 {
    chrono::Utc::now().timestamp()
}

async fn register_session(
    session_id: &str,
    agent: &str,
    message: &str,
    working_dir: Option<String>,
) {
    let now = now_timestamp();
    let session = CLISession {
        id: session_id.to_string(),
        agent: agent.to_string(),
        command: message.to_string(),
        working_dir,
        is_active: true,
        created_at: now,
        last_activity: now,
    };

    let mut sessions = SESSIONS.lock().await;
    sessions.insert(session_id.to_string(), session);
}

async fn is_session_active(session_id: &str) -> bool {
    let sessions = SESSIONS.lock().await;
    sessions
        .get(session_id)
        .map(|session| session.is_active)
        .unwrap_or(false)
}

async fn remove_session(session_id: &str) {
    let mut sessions = SESSIONS.lock().await;
    sessions.remove(session_id);
}

async fn emit_stream(
    app: &tauri::AppHandle,
    session_id: &str,
    content: impl Into<String>,
    finished: bool,
) {
    let _ = app.emit(
        "cli-stream",
        StreamChunk {
            session_id: session_id.to_string(),
            content: content.into(),
            finished,
        },
    );
}

fn build_shell_mode_notice(
    agent: &str,
    message: &str,
    working_dir: Option<&str>,
    execution_mode: Option<&str>,
    permission_mode: Option<&str>,
    dangerous_bypass: bool,
    resume_session_id: Option<&str>,
) -> String {
    let mut lines = vec![
        "Robot Workbench shell mode".to_string(),
        format!("Agent: {}", agent),
        "Local CLI orchestration has been removed from the desktop app.".to_string(),
        "Wire your Python WebSocket backend into this command path next.".to_string(),
    ];

    if let Some(dir) = working_dir.filter(|dir| !dir.trim().is_empty()) {
        lines.push(format!("Working directory: {}", dir));
    }
    if let Some(mode) = execution_mode.filter(|mode| !mode.trim().is_empty()) {
        lines.push(format!("Requested execution mode: {}", mode));
    }
    if let Some(mode) = permission_mode.filter(|mode| !mode.trim().is_empty()) {
        lines.push(format!("Requested permission mode: {}", mode));
    }
    if dangerous_bypass {
        lines.push("Danger bypass flag requested.".to_string());
    }
    if let Some(resume_id) = resume_session_id.filter(|id| !id.trim().is_empty()) {
        lines.push(format!("Resume session request: {}", resume_id));
    }
    if !message.trim().is_empty() {
        lines.push(format!("Queued user message: {}", message.trim()));
    }

    format!("{}\n", lines.join("\n"))
}

#[tauri::command]
pub async fn execute_persistent_cli_command(
    app: tauri::AppHandle,
    session_id: String,
    agent: String,
    message: String,
    working_dir: Option<String>,
    #[allow(non_snake_case)] executionMode: Option<String>,
    #[allow(non_snake_case)] dangerousBypass: Option<bool>,
    #[allow(non_snake_case)] permissionMode: Option<String>,
    #[allow(non_snake_case)] resumeSessionId: Option<String>,
) -> Result<(), String> {
    let normalized_working_dir = working_dir
        .filter(|dir| !dir.trim().is_empty())
        .or_else(|| {
            std::env::current_dir()
                .ok()
                .map(|path| path.to_string_lossy().to_string())
        });

    register_session(
        &session_id,
        &agent,
        &message,
        normalized_working_dir.clone(),
    )
    .await;

    let notice = build_shell_mode_notice(
        &agent,
        &message,
        normalized_working_dir.as_deref(),
        executionMode.as_deref(),
        permissionMode.as_deref(),
        dangerousBypass.unwrap_or(false),
        resumeSessionId.as_deref(),
    );

    let app_clone = app.clone();
    let session_id_clone = session_id.clone();

    tokio::spawn(async move {
        emit_stream(&app_clone, &session_id_clone, notice, false).await;
        tokio::time::sleep(tokio::time::Duration::from_millis(120)).await;

        if !is_session_active(&session_id_clone).await {
            return;
        }

        emit_stream(&app_clone, &session_id_clone, String::new(), true).await;
        remove_session(&session_id_clone).await;
    });

    Ok(())
}

#[tauri::command]
pub async fn respond_permission(
    session_id: String,
    request_id: String,
    approved: bool,
) -> Result<(), String> {
    let _ = (session_id, request_id, approved);
    Ok(())
}

#[tauri::command]
pub async fn execute_cli_command(
    app: tauri::AppHandle,
    session_id: String,
    command: String,
    args: Vec<String>,
    working_dir: Option<String>,
    #[allow(non_snake_case)] executionMode: Option<String>,
    #[allow(non_snake_case)] dangerousBypass: Option<bool>,
    #[allow(non_snake_case)] permissionMode: Option<String>,
) -> Result<(), String> {
    execute_persistent_cli_command(
        app,
        session_id,
        command,
        args.join(" "),
        working_dir,
        executionMode,
        dangerousBypass,
        permissionMode,
        None,
    )
    .await
}

#[tauri::command]
pub async fn execute_claude_command(
    app: tauri::AppHandle,
    #[allow(non_snake_case)] sessionId: String,
    message: String,
    #[allow(non_snake_case)] workingDir: Option<String>,
    #[allow(non_snake_case)] permissionMode: Option<String>,
    #[allow(non_snake_case)] resumeSessionId: Option<String>,
) -> Result<(), String> {
    execute_persistent_cli_command(
        app,
        sessionId,
        "claude".to_string(),
        message,
        workingDir,
        None,
        None,
        permissionMode,
        resumeSessionId,
    )
    .await
}

#[tauri::command]
pub async fn execute_codex_command(
    app: tauri::AppHandle,
    #[allow(non_snake_case)] sessionId: String,
    message: String,
    #[allow(non_snake_case)] workingDir: Option<String>,
    #[allow(non_snake_case)] executionMode: Option<String>,
    #[allow(non_snake_case)] dangerousBypass: Option<bool>,
    #[allow(non_snake_case)] permissionMode: Option<String>,
) -> Result<(), String> {
    execute_persistent_cli_command(
        app,
        sessionId,
        "codex".to_string(),
        message,
        workingDir,
        executionMode,
        dangerousBypass,
        permissionMode,
        None,
    )
    .await
}

#[tauri::command]
pub async fn execute_gemini_command(
    app: tauri::AppHandle,
    #[allow(non_snake_case)] sessionId: String,
    message: String,
    #[allow(non_snake_case)] workingDir: Option<String>,
    #[allow(non_snake_case)] approvalMode: Option<String>,
) -> Result<(), String> {
    execute_persistent_cli_command(
        app,
        sessionId,
        "gemini".to_string(),
        message,
        workingDir,
        None,
        None,
        approvalMode,
        None,
    )
    .await
}

#[tauri::command]
pub async fn execute_ollama_command(
    app: tauri::AppHandle,
    #[allow(non_snake_case)] sessionId: String,
    message: String,
    #[allow(non_snake_case)] workingDir: Option<String>,
) -> Result<(), String> {
    execute_persistent_cli_command(
        app,
        sessionId,
        "ollama".to_string(),
        message,
        workingDir,
        None,
        None,
        None,
        None,
    )
    .await
}

#[tauri::command]
pub async fn execute_test_command(
    app: tauri::AppHandle,
    #[allow(non_snake_case)] sessionId: String,
    message: String,
    #[allow(non_snake_case)] workingDir: Option<String>,
) -> Result<(), String> {
    register_session(&sessionId, "test", &message, workingDir).await;

    let app_clone = app.clone();
    let session_id_clone = sessionId.clone();

    tokio::spawn(async move {
        let lines = vec![
            "Robot Workbench test stream".to_string(),
            format!("Echo: {}", message),
            "Desktop shell event streaming is working.".to_string(),
        ];

        for line in lines {
            if !is_session_active(&session_id_clone).await {
                return;
            }
            emit_stream(&app_clone, &session_id_clone, format!("{}\n", line), false).await;
            tokio::time::sleep(tokio::time::Duration::from_millis(120)).await;
        }

        if !is_session_active(&session_id_clone).await {
            return;
        }

        emit_stream(&app_clone, &session_id_clone, String::new(), true).await;
        remove_session(&session_id_clone).await;
    });

    Ok(())
}

pub async fn cleanup_cli_sessions() -> Result<(), String> {
    let cutoff = now_timestamp() - SESSION_TIMEOUT_SECONDS;
    let stale_ids: Vec<String> = {
        let sessions = SESSIONS.lock().await;
        sessions
            .iter()
            .filter(|(_, session)| session.last_activity < cutoff)
            .map(|(session_id, _)| session_id.clone())
            .collect()
    };

    if stale_ids.is_empty() {
        return Ok(());
    }

    let mut sessions = SESSIONS.lock().await;
    for session_id in stale_ids {
        sessions.remove(&session_id);
    }

    Ok(())
}

pub async fn get_sessions_status() -> Result<SessionStatus, String> {
    let sessions = SESSIONS.lock().await;
    let mut active_sessions: Vec<CLISession> = sessions.values().cloned().collect();
    active_sessions.sort_by(|a, b| b.created_at.cmp(&a.created_at));

    Ok(SessionStatus {
        total_sessions: active_sessions.len(),
        active_sessions,
    })
}

pub async fn terminate_session_by_id(session_id: &str) -> Result<(), String> {
    let mut sessions = SESSIONS.lock().await;
    if sessions.remove(session_id).is_some() {
        Ok(())
    } else {
        Err(format!("Session not found: {}", session_id))
    }
}

pub async fn terminate_all_active_sessions() -> Result<(), String> {
    let mut sessions = SESSIONS.lock().await;
    sessions.clear();
    Ok(())
}

pub async fn send_quit_to_session(session_id: &str) -> Result<(), String> {
    terminate_session_by_id(session_id).await
}

#[tauri::command]
pub fn open_file_in_editor(file_path: String) -> Result<(), String> {
    let path = Path::new(&file_path);

    if !path.exists() {
        return Err(format!("File does not exist: {}", file_path));
    }

    #[cfg(target_os = "macos")]
    let mut child = StdCommand::new("open")
        .arg("-t")
        .arg(file_path)
        .spawn()
        .map_err(|e| format!("Failed to open file: {}", e))?;

    #[cfg(target_os = "windows")]
    let mut child = StdCommand::new("cmd")
        .args(["/C", "start", "", &file_path])
        .spawn()
        .map_err(|e| format!("Failed to open file: {}", e))?;

    #[cfg(target_os = "linux")]
    let mut child = StdCommand::new("xdg-open")
        .arg(file_path)
        .spawn()
        .map_err(|e| format!("Failed to open file: {}", e))?;

    std::thread::spawn(move || {
        let _ = child.wait();
    });

    Ok(())
}
