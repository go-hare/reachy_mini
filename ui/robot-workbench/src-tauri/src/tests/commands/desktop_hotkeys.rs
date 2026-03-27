#[cfg(test)]
mod tests {
    #[test]
    fn desktop_shell_no_longer_registers_legacy_hotkeys() {
        let lib_rs = include_str!("../../lib.rs");
        let cargo_toml = include_str!("../../../Cargo.toml");

        assert!(
            !cargo_toml.contains("tauri-plugin-global-shortcut"),
            "Cargo.toml should not keep the global shortcut plugin once desktop hotkeys are removed"
        );
        assert!(
            !lib_rs.contains(".accelerator("),
            "native menu entries should not expose accelerator hotkeys after the hotkey removal pass"
        );
        assert!(
            !lib_rs.contains("shortcut://open-settings"),
            "lib.rs should no longer emit settings shortcut events"
        );
        assert!(
            !lib_rs.contains("shortcut://toggle-chat"),
            "lib.rs should no longer emit chat toggle shortcut events"
        );
        assert!(
            !lib_rs.contains("shortcut://toggle-chat-history"),
            "lib.rs should no longer emit chat history shortcut events"
        );
        assert!(
            !lib_rs.contains("shortcut://copy-project-path"),
            "lib.rs should no longer emit copy path shortcut events"
        );
    }
}
