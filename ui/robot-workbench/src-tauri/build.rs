fn main() {
    // Link against AVFoundation framework on macOS
    #[cfg(target_os = "macos")]
    {
        println!("cargo:rustc-link-lib=framework=AVFoundation");
        println!("cargo:rustc-link-lib=framework=CoreLocation");
        println!("cargo:rustc-link-lib=framework=CoreWLAN");
        println!("cargo:rustc-link-lib=framework=Network");
        println!("cargo:rustc-link-lib=framework=CoreBluetooth");

        // Compile the CoreBluetooth permission helper.
        cc::Build::new()
            .file("src/permissions/bluetooth_permission.m")
            .flag("-fobjc-arc")
            .compile("bluetooth_permission");

        // Compile the Objective-C NWBrowser helper for local network
        // permission detection (Apple TN3179 recommended approach).
        cc::Build::new()
            .file("src/permissions/nw_local_network.m")
            .flag("-fobjc-arc")
            .compile("nw_local_network");

        // Compile the CoreWLAN WiFi scanner + location permission helper.
        // Requires macOS 11+ for CLLocationManager.authorizationStatus instance property.
        cc::Build::new()
            .file("src/wifi/corewlan_scan.m")
            .flag("-fobjc-arc")
            .flag("-mmacosx-version-min=11.0")
            .compile("corewlan_scan");
    }

    // Link against libX11 on Linux for XInitThreads()
    // Required to prevent xcb threading crashes with WebKitGTK
    #[cfg(target_os = "linux")]
    {
        println!("cargo:rustc-link-lib=X11");
    }

    // On Windows, RC.EXE cannot handle apostrophes or other special characters in file paths.
    // tauri-winres canonicalizes the icon path (resolving symlinks/junctions), so if the real
    // user profile folder contains an apostrophe (e.g. C:\Users\hightower's\...), the build
    // fails even when accessed via a symlink. Workaround: copy the icon to a root-level
    // directory whose canonical path is guaranteed to be clean.
    #[cfg(target_os = "windows")]
    {
        use std::path::{Path, PathBuf};

        let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
        let icon_path = Path::new(&manifest_dir).join("icons").join("icon.ico");

        let needs_workaround = icon_path
            .canonicalize()
            .map(|p| {
                let s = p.to_string_lossy();
                s.contains('\'') || s.contains('&') || s.contains('#')
            })
            .unwrap_or(false);

        if needs_workaround {
            let drive = std::env::var("SYSTEMDRIVE").unwrap_or_else(|_| "C:".to_string());
            let safe_dir = PathBuf::from(format!("{}\\tauri-build-tmp", drive));
            std::fs::create_dir_all(&safe_dir).unwrap_or_else(|e| {
                panic!(
                    "Cannot create {}: {}. \
                     Your user folder contains characters that break RC.EXE. \
                     Either clone the project to a path without special characters, \
                     or create this directory manually.",
                    safe_dir.display(),
                    e
                )
            });
            let safe_icon = safe_dir.join("icon.ico");
            std::fs::copy(&icon_path, &safe_icon).unwrap_or_else(|e| {
                panic!("Failed to copy icon to {}: {}", safe_icon.display(), e)
            });

            let windows_attrs = tauri_build::WindowsAttributes::new().window_icon_path(&safe_icon);
            let attrs = tauri_build::Attributes::new().windows_attributes(windows_attrs);
            tauri_build::try_build(attrs).expect("Failed to run Tauri build script");
            return;
        }
    }

    tauri_build::build()
}
