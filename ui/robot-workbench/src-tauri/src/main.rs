// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // X11 is not thread-safe by default. WebKitGTK + Tauri spawn multiple
    // threads that access the display connection, causing xcb sequence errors.
    // Must be called before any GTK/X11 initialization.
    #[cfg(target_os = "linux")]
    {
        extern "C" {
            fn XInitThreads() -> std::ffi::c_int;
        }
        unsafe {
            XInitThreads();
        }
    }

    reachy_mini_control_lib::run()
}
