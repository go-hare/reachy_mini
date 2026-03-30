/// USB Device Detection Module
///
/// This module provides USB device detection with two strategies:
/// - Windows: Event-driven detection using WM_DEVICECHANGE (NO polling, NO terminal flicker)
/// - Other platforms: Direct detection (no background monitoring needed)
mod monitor;

pub use monitor::start_monitor;

/// Check if Reachy Mini USB robot is connected
///
/// On Windows: Uses event-driven detection (no polling, no terminal flicker)
/// On other platforms: Direct check using serialport
#[tauri::command]
pub fn check_usb_robot() -> Result<Option<String>, String> {
    Ok(monitor::get_reachy_port())
}
