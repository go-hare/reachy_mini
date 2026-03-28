use crate::services::robot_daemon_service::{
    MujocoViewerServiceManager, RobotDaemonManager, RobotDaemonProcessStatus,
};
use serde::Serialize;
use std::time::Duration;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MujocoViewerProbeResult {
    pub ok: bool,
    pub status: Option<u16>,
    pub error: Option<String>,
}

#[tauri::command]
pub fn get_robot_sim_daemon_status(
    manager: tauri::State<'_, RobotDaemonManager>,
) -> Result<RobotDaemonProcessStatus, String> {
    manager.status()
}

#[tauri::command]
pub fn start_robot_sim_daemon(
    manager: tauri::State<'_, RobotDaemonManager>,
    #[allow(non_snake_case)] workingDir: Option<String>,
) -> Result<RobotDaemonProcessStatus, String> {
    manager.start_sim(workingDir)
}

#[tauri::command]
pub fn stop_robot_sim_daemon(
    manager: tauri::State<'_, RobotDaemonManager>,
) -> Result<RobotDaemonProcessStatus, String> {
    manager.stop()
}

#[tauri::command]
pub fn get_mujoco_viewer_service_status(
    manager: tauri::State<'_, MujocoViewerServiceManager>,
) -> Result<RobotDaemonProcessStatus, String> {
    manager.status()
}

#[tauri::command]
pub fn start_mujoco_viewer_service(
    manager: tauri::State<'_, MujocoViewerServiceManager>,
    #[allow(non_snake_case)] workingDir: Option<String>,
    #[allow(non_snake_case)] launchCommand: String,
) -> Result<RobotDaemonProcessStatus, String> {
    manager.start(workingDir, launchCommand)
}

#[tauri::command]
pub fn stop_mujoco_viewer_service(
    manager: tauri::State<'_, MujocoViewerServiceManager>,
) -> Result<RobotDaemonProcessStatus, String> {
    manager.stop()
}

#[tauri::command]
pub async fn probe_mujoco_viewer_url(url: String) -> Result<MujocoViewerProbeResult, String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .redirect(reqwest::redirect::Policy::limited(5))
        .build()
        .map_err(|error| format!("Failed to create viewer probe client: {error}"))?;

    match client.get(&url).send().await {
        Ok(response) => {
            let status = response.status();
            let ok = status.is_success() || status.is_redirection();
            let error = if ok {
                None
            } else {
                Some(format!("Viewer responded with HTTP {}", status.as_u16()))
            };

            Ok(MujocoViewerProbeResult {
                ok,
                status: Some(status.as_u16()),
                error,
            })
        }
        Err(error) => Ok(MujocoViewerProbeResult {
            ok: false,
            status: None,
            error: Some(error.to_string()),
        }),
    }
}
