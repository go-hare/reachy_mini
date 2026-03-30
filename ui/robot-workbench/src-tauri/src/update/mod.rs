/// Update module for managing daemon updates
///
/// This module provides functionality to check for and install daemon updates
/// independently of the Python daemon's update routes. It queries GitHub Releases
/// for version info and manages the local venv using pip.
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager, State};

use crate::daemon::DaemonState;

const GITHUB_REPO: &str = "pollen-robotics/reachy_mini";

/// PEP 440 pre-release suffixes mapped to semver labels.
/// Order matters: checked first-to-last, "rc" before single-char "a"/"b"
/// to avoid "rc" being split on 'c' by the 'a' or 'b' rule.
const PEP440_SUFFIXES: &[(&str, &str)] = &[("rc", "rc"), ("a", "alpha"), ("b", "beta")];

// ============================================================================
// TYPES
// ============================================================================

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct DaemonUpdateInfo {
    pub current_version: String,
    pub available_version: String,
    pub is_available: bool,
}

#[derive(Debug, Deserialize)]
struct GitHubRelease {
    tag_name: String,
}

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

/// Get the path to the local venv SOURCE directory.
/// This is the directory that contains the .venv that uv-trampoline will copy.
/// - In dev: src-tauri/binaries/.venv
/// - In production (macOS): ~/Library/Application Support/com.pollen-robotics.reachy-mini/.venv
///   (fallback: App.app/Contents/Resources/.venv)
/// - In production (Windows): %LOCALAPPDATA%\Reachy Mini Control\.venv
fn get_local_venv_path(app_handle: &AppHandle) -> Result<PathBuf, String> {
    #[cfg(target_os = "windows")]
    {
        let program_files =
            std::env::var("ProgramFiles").unwrap_or_else(|_| "C:\\Program Files".to_string());
        let program_files_dir = PathBuf::from(program_files)
            .join("Reachy Mini Control")
            .join("binaries");

        if program_files_dir.join(".venv").exists() {
            log::info!("[update] Using Program Files venv: {:?}", program_files_dir);
            return Ok(program_files_dir);
        }

        let resource_dir = app_handle
            .path()
            .resource_dir()
            .map_err(|e| format!("Failed to get resource dir: {}", e))?;
        let binaries_dir = resource_dir.join("binaries");

        if binaries_dir.join(".venv").exists() {
            log::info!("[update] Using dev venv: {:?}", binaries_dir);
            return Ok(binaries_dir);
        }

        Err(format!(
            "Venv not found. Checked {:?} and {:?}",
            program_files_dir, binaries_dir
        ))
    }

    #[cfg(not(target_os = "windows"))]
    {
        let exe_path =
            std::env::current_exe().map_err(|e| format!("Failed to get exe path: {}", e))?;
        let exe_dir = exe_path
            .parent()
            .ok_or_else(|| "Failed to get exe parent directory".to_string())?;

        log::info!("[update] Executable directory: {:?}", exe_dir);

        // In development, Tauri copies resources to target/debug/
        if exe_dir.ends_with("target/debug") || exe_dir.ends_with("target\\debug") {
            return resolve_dev_venv_path(exe_dir);
        }

        #[cfg(target_os = "macos")]
        {
            if let Some(path) = resolve_macos_prod_venv_path(exe_dir) {
                return Ok(path);
            }
        }

        // Fallback: Use resource_dir from Tauri API
        let resource_dir = app_handle
            .path()
            .resource_dir()
            .map_err(|e| format!("Failed to get resource dir: {}", e))?;
        let binaries_dir = resource_dir.join("binaries");

        if binaries_dir.join(".venv").exists() {
            log::info!("[update] Using resource_dir venv: {:?}", binaries_dir);
            Ok(binaries_dir)
        } else {
            Err(format!(
                "Venv not found. Checked exe_dir, macOS Resources, and resource_dir: {:?}",
                binaries_dir.join(".venv")
            ))
        }
    }
}

/// Resolve venv path in a dev environment (target/debug/).
#[cfg(not(target_os = "windows"))]
fn resolve_dev_venv_path(exe_dir: &Path) -> Result<PathBuf, String> {
    let target_debug_dir = exe_dir.to_path_buf();

    if target_debug_dir.join(".venv").exists() {
        log::info!(
            "[update] Using target/debug venv (runtime copy): {:?}",
            target_debug_dir
        );
        return Ok(target_debug_dir);
    }

    let src_tauri_dir = exe_dir
        .parent()
        .and_then(|p| p.parent())
        .ok_or_else(|| "Failed to navigate to src-tauri directory".to_string())?;

    let binaries_dir = if src_tauri_dir.ends_with("src-tauri") {
        src_tauri_dir.join("binaries")
    } else {
        src_tauri_dir.join("src-tauri").join("binaries")
    };

    if binaries_dir.join(".venv").exists() {
        log::info!("[update] Using dev binaries venv: {:?}", binaries_dir);
        return Ok(binaries_dir);
    }

    Err(format!(
        "Dev venv not found in {:?} or {:?}",
        target_debug_dir.join(".venv"),
        binaries_dir.join(".venv")
    ))
}

/// Resolve venv path for macOS production builds.
/// Priority: Application Support (persists across updates) > App bundle Resources.
#[cfg(target_os = "macos")]
fn resolve_macos_prod_venv_path(exe_dir: &Path) -> Option<PathBuf> {
    // Application Support directory (externalized venv, persists across Tauri updates)
    if let Some(data_dir) = dirs::data_dir() {
        let app_support_dir = data_dir.join("com.pollen-robotics.reachy-mini");
        if app_support_dir.join(".venv").exists() {
            log::info!(
                "[update] Using Application Support venv: {:?}",
                app_support_dir
            );
            return Some(app_support_dir);
        }
    }

    // App bundle Resources (before externalization or as fallback)
    if let Some(macos_dir) = exe_dir.parent() {
        let resources_dir = macos_dir.join("Resources");
        if resources_dir.join(".venv").exists() {
            log::info!("[update] Using production venv: {:?}", resources_dir);
            return Some(resources_dir);
        }
    }

    None
}

/// Get the currently installed version of reachy-mini from the local venv.
fn get_local_daemon_version(venv_path: &Path) -> Result<String, String> {
    let site_packages = get_site_packages_path(venv_path)?;

    let entries = std::fs::read_dir(&site_packages)
        .map_err(|e| format!("Failed to read site-packages: {}", e))?;

    for entry in entries {
        let entry = entry.map_err(|e| format!("Failed to read entry: {}", e))?;
        let name = entry.file_name().to_string_lossy().to_string();

        if name.starts_with("reachy_mini-") && name.ends_with(".dist-info") {
            let metadata_path = entry.path().join("METADATA");
            if metadata_path.exists() {
                let content = std::fs::read_to_string(&metadata_path)
                    .map_err(|e| format!("Failed to read METADATA: {}", e))?;

                for line in content.lines() {
                    if let Some(version) = line.strip_prefix("Version: ") {
                        return Ok(version.trim().to_string());
                    }
                }
            }
        }
    }

    Err("reachy-mini version not found in venv".to_string())
}

/// Locate the site-packages directory inside a venv (cross-platform).
fn get_site_packages_path(venv_path: &Path) -> Result<PathBuf, String> {
    #[cfg(target_os = "windows")]
    {
        let path = venv_path.join(".venv").join("Lib").join("site-packages");
        if path.exists() {
            return Ok(path);
        }
        Err(format!("Site-packages not found at {:?}", path))
    }

    #[cfg(not(target_os = "windows"))]
    {
        let lib_dir = venv_path.join(".venv").join("lib");
        if !lib_dir.exists() {
            return Err(format!("Venv lib dir not found at {:?}", lib_dir));
        }

        let python_dir = std::fs::read_dir(&lib_dir)
            .map_err(|e| format!("Failed to read lib dir: {}", e))?
            .filter_map(|e| e.ok())
            .find(|e| e.file_name().to_string_lossy().starts_with("python3"))
            .ok_or_else(|| format!("No python3.x directory found in {:?}", lib_dir))?;

        log::info!(
            "[update] Detected Python version dir: {:?}",
            python_dir.file_name()
        );

        let path = python_dir.path().join("site-packages");
        if path.exists() {
            Ok(path)
        } else {
            Err(format!("Site-packages not found at {:?}", path))
        }
    }
}

/// Get the latest version available from GitHub Releases.
///
/// - Stable: GET /repos/{repo}/releases/latest (excludes pre-releases)
/// - Pre-release: GET /repos/{repo}/releases?per_page=1 (most recent, any type)
async fn get_github_version(pre_release: bool) -> Result<String, String> {
    let url = if pre_release {
        format!(
            "https://api.github.com/repos/{}/releases?per_page=1",
            GITHUB_REPO
        )
    } else {
        format!(
            "https://api.github.com/repos/{}/releases/latest",
            GITHUB_REPO
        )
    };

    log::info!("[update] Fetching GitHub release from: {}", url);

    let client = reqwest::Client::new();
    let response = client
        .get(&url)
        .header("Accept", "application/vnd.github.v3+json")
        .header("User-Agent", "reachy-mini-desktop-app")
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("Failed to fetch GitHub releases: {}", e))?;

    if !response.status().is_success() {
        return Err(format!("GitHub API returned status: {}", response.status()));
    }

    let tag = if pre_release {
        let releases: Vec<GitHubRelease> = response
            .json()
            .await
            .map_err(|e| format!("Failed to parse GitHub JSON: {}", e))?;

        releases
            .first()
            .ok_or_else(|| "No releases found on GitHub".to_string())?
            .tag_name
            .clone()
    } else {
        let release: GitHubRelease = response
            .json()
            .await
            .map_err(|e| format!("Failed to parse GitHub JSON: {}", e))?;

        release.tag_name
    };

    let version = tag.strip_prefix('v').unwrap_or(&tag).to_string();
    log::info!("[update] GitHub version: {}", version);
    Ok(version)
}

/// Try to convert a PEP 440 pre-release suffix into a semver pre-release.
/// Returns None if the separator is not found in `patch_part`.
fn try_parse_pep440_suffix(
    major: &str,
    minor: &str,
    patch_part: &str,
    separator: &str,
    semver_label: &str,
) -> Option<Result<semver::Version, String>> {
    if !patch_part.contains(separator) {
        return None;
    }
    let parts: Vec<&str> = patch_part.splitn(2, separator).collect();
    if parts.len() != 2 || parts[0].is_empty() || parts[1].is_empty() {
        return None;
    }
    let clean = format!(
        "{}.{}.{}-{}.{}",
        major, minor, parts[0], semver_label, parts[1]
    );
    Some(semver::Version::parse(&clean).map_err(|e| {
        format!(
            "Failed to parse {} version '{}': {}",
            semver_label, clean, e
        )
    }))
}

/// Parse a PEP 440 version string into semver.
///
/// Handles: "1.4.0", "1.2.5rc1", "1.3.1.dev0", "1.2.5a1", "1.2.5b2"
fn parse_version(version_str: &str) -> Result<semver::Version, String> {
    if let Ok(ver) = semver::Version::parse(version_str) {
        return Ok(ver);
    }

    let parts: Vec<&str> = version_str.split('.').collect();
    if parts.len() < 3 {
        return Err(format!("Invalid version string: {}", version_str));
    }

    let major = parts[0];
    let minor = parts[1];
    let patch_part = parts[2];

    // PEP 440 ".devN" suffix: "1.3.1.dev0" -> "1.3.1-0.dev.0"
    // Uses a leading numeric identifier (0) so semver sorts it before all
    // alphanumeric pre-releases (alpha, beta, rc), matching PEP 440 ordering.
    if parts.len() >= 4 && parts[3].starts_with("dev") {
        let dev_num = &parts[3][3..];
        let clean = format!("{}.{}.{}-0.dev.{}", major, minor, patch_part, dev_num);
        return semver::Version::parse(&clean)
            .map_err(|e| format!("Failed to parse dev version '{}': {}", clean, e));
    }

    for (separator, label) in PEP440_SUFFIXES {
        if let Some(result) = try_parse_pep440_suffix(major, minor, patch_part, separator, label) {
            return result;
        }
    }

    Err(format!("Could not parse version: {}", version_str))
}

/// Check if a new version is available.
fn is_update_available(current: &str, available: &str) -> Result<bool, String> {
    let current_ver = parse_version(current)?;
    let available_ver = parse_version(available)?;

    Ok(available_ver > current_ver)
}

/// Get the platform-specific pip executable path inside a venv.
fn get_pip_path(venv_path: &Path) -> Result<PathBuf, String> {
    #[cfg(target_os = "windows")]
    let pip_path = venv_path.join(".venv").join("Scripts").join("pip.exe");

    #[cfg(not(target_os = "windows"))]
    let pip_path = venv_path.join(".venv").join("bin").join("pip");

    if !pip_path.exists() {
        return Err(format!("pip not found at {:?}", pip_path));
    }

    Ok(pip_path)
}

/// Run a pip command asynchronously and return (stdout, stderr).
/// Uses tokio::process to avoid blocking the async runtime.
async fn run_pip(
    pip_path: &Path,
    args: &[&str],
    context: &str,
) -> Result<(String, String), String> {
    log::info!(
        "[update] Running pip ({}): {:?} {:?}",
        context,
        pip_path,
        args
    );

    let output = tokio::process::Command::new(pip_path)
        .args(args)
        .output()
        .await
        .map_err(|e| format!("Failed to run pip ({}): {}", context, e))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !stdout.is_empty() {
        log::info!("[update] pip stdout ({}):\n{}", context, stdout);
    }
    if !stderr.is_empty() {
        log::info!("[update] pip stderr ({}):\n{}", context, stderr);
    }

    if !output.status.success() {
        return Err(format!(
            "pip {} failed with exit code {:?}:\n{}",
            context,
            output.status.code(),
            stderr
        ));
    }

    Ok((stdout, stderr))
}

// ============================================================================
// TAURI COMMANDS
// ============================================================================

/// Check if an update is available for the daemon.
#[tauri::command]
pub async fn check_daemon_update(
    app_handle: AppHandle,
    pre_release: bool,
) -> Result<DaemonUpdateInfo, String> {
    log::info!(
        "[update] Checking for daemon updates (pre_release: {})",
        pre_release
    );

    let venv_path = get_local_venv_path(&app_handle)?;
    let current_version = get_local_daemon_version(&venv_path)?;
    log::info!("[update] Current version: {}", current_version);

    let available_version = get_github_version(pre_release).await?;
    log::info!("[update] Available version: {}", available_version);

    let is_available = is_update_available(&current_version, &available_version)?;
    log::info!("[update] Update available: {}", is_available);

    Ok(DaemonUpdateInfo {
        current_version,
        available_version,
        is_available,
    })
}

/// Update the daemon to the latest version.
#[tauri::command]
pub async fn update_daemon(
    app_handle: AppHandle,
    state: State<'_, DaemonState>,
    pre_release: bool,
) -> Result<String, String> {
    log::info!(
        "[update] Starting daemon update (pre_release: {})",
        pre_release
    );

    log::info!("[update] Stopping daemon...");
    crate::stop_daemon(app_handle.clone(), state.clone())?;

    tokio::time::sleep(tokio::time::Duration::from_millis(1000)).await;

    let venv_path = get_local_venv_path(&app_handle)?;
    let pip_path = get_pip_path(&venv_path)?;
    log::info!("[update] Using pip at: {:?}", pip_path);

    // Upgrade reachy-mini
    let mut args = vec!["install", "--upgrade", "reachy-mini"];
    if pre_release {
        args.push("--pre");
    }

    run_pip(&pip_path, &args, "reachy-mini upgrade").await?;

    log::info!("[update] Daemon updated successfully!");
    log::info!("[update] uv-trampoline will copy the new venv when daemon starts again");

    Ok("Daemon updated successfully. Reconnect to use the new version.".to_string())
}

// ============================================================================
// TESTS
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // ========================================================================
    // parse_version: standard semver
    // ========================================================================

    #[test]
    fn parse_standard_semver() {
        let v = parse_version("1.4.0").unwrap();
        assert_eq!(v, semver::Version::new(1, 4, 0));
    }

    #[test]
    fn parse_standard_semver_with_patch() {
        let v = parse_version("2.0.13").unwrap();
        assert_eq!(v, semver::Version::new(2, 0, 13));
    }

    // ========================================================================
    // parse_version: PEP 440 pre-release suffixes
    // ========================================================================

    #[test]
    fn parse_rc_version() {
        let v = parse_version("1.2.5rc1").unwrap();
        assert_eq!(v.major, 1);
        assert_eq!(v.minor, 2);
        assert_eq!(v.patch, 5);
        assert!(!v.pre.is_empty());
    }

    #[test]
    fn parse_alpha_version() {
        let v = parse_version("1.2.5a1").unwrap();
        assert_eq!(v.major, 1);
        assert_eq!(v.minor, 2);
        assert_eq!(v.patch, 5);
        assert!(!v.pre.is_empty());
    }

    #[test]
    fn parse_beta_version() {
        let v = parse_version("1.2.5b2").unwrap();
        assert_eq!(v.major, 1);
        assert_eq!(v.minor, 2);
        assert_eq!(v.patch, 5);
        assert!(!v.pre.is_empty());
    }

    #[test]
    fn parse_dev_version() {
        let v = parse_version("1.3.1.dev0").unwrap();
        assert_eq!(v.major, 1);
        assert_eq!(v.minor, 3);
        assert_eq!(v.patch, 1);
        assert!(!v.pre.is_empty());
    }

    // ========================================================================
    // parse_version: edge cases
    // ========================================================================

    #[test]
    fn parse_rejects_too_short() {
        assert!(parse_version("1.2").is_err());
    }

    #[test]
    fn parse_rejects_empty() {
        assert!(parse_version("").is_err());
    }

    #[test]
    fn parse_rejects_garbage() {
        assert!(parse_version("not-a-version").is_err());
    }

    // ========================================================================
    // is_update_available: comparison logic
    // ========================================================================

    #[test]
    fn newer_version_is_available() {
        assert!(is_update_available("1.2.0", "1.3.0").unwrap());
    }

    #[test]
    fn same_version_not_available() {
        assert!(!is_update_available("1.4.0", "1.4.0").unwrap());
    }

    #[test]
    fn older_version_not_available() {
        assert!(!is_update_available("2.0.0", "1.9.0").unwrap());
    }

    #[test]
    fn patch_bump_is_available() {
        assert!(is_update_available("1.4.0", "1.4.1").unwrap());
    }

    #[test]
    fn rc_is_less_than_release() {
        assert!(is_update_available("1.2.5rc1", "1.2.5").unwrap());
    }

    #[test]
    fn alpha_is_less_than_beta() {
        assert!(is_update_available("1.2.5a1", "1.2.5b1").unwrap());
    }

    #[test]
    fn beta_is_less_than_rc() {
        assert!(is_update_available("1.2.5b1", "1.2.5rc1").unwrap());
    }

    #[test]
    fn dev_is_less_than_release() {
        assert!(is_update_available("1.3.1.dev0", "1.3.1").unwrap());
    }

    // ========================================================================
    // Pre-release ordering: full chain
    // ========================================================================

    #[test]
    fn full_prerelease_ordering() {
        let dev = parse_version("1.2.5.dev0").unwrap();
        let alpha = parse_version("1.2.5a1").unwrap();
        let beta = parse_version("1.2.5b1").unwrap();
        let rc = parse_version("1.2.5rc1").unwrap();
        let release = parse_version("1.2.5").unwrap();

        assert!(dev < alpha, "dev < alpha");
        assert!(alpha < beta, "alpha < beta");
        assert!(beta < rc, "beta < rc");
        assert!(rc < release, "rc < release");
    }

    // ========================================================================
    // try_parse_pep440_suffix: unit tests
    // ========================================================================

    #[test]
    fn pep440_suffix_returns_none_when_not_found() {
        assert!(try_parse_pep440_suffix("1", "2", "5", "rc", "rc").is_none());
    }

    #[test]
    fn pep440_suffix_parses_rc() {
        let result = try_parse_pep440_suffix("1", "2", "5rc1", "rc", "rc");
        assert!(result.is_some());
        let v = result.unwrap().unwrap();
        assert_eq!(v.major, 1);
        assert_eq!(v.patch, 5);
    }
}
