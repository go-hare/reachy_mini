use crate::services::robot_daemon_service::{RobotDaemonManager, RobotDaemonProcessStatus};

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
