#[cfg(test)]
mod tests {
    use serde_json::Value;

    use crate::models::AppSettings;

    fn agent_from(value: &Value) -> Option<&str> {
        value.get("default_cli_agent").and_then(|v| v.as_str())
    }

    fn round_trip(mut raw: Value) -> Value {
        let settings: AppSettings =
            serde_json::from_value(raw.take()).expect("deserialize settings");
        serde_json::to_value(settings).expect("serialize settings")
    }

    #[test]
    fn default_settings_include_default_agent_field() {
        let value = serde_json::to_value(AppSettings::default()).expect("serialize defaults");
        assert_eq!(
            agent_from(&value),
            Some("claude"),
            "default AppSettings should expose the default CLI agent"
        );
    }

    #[test]
    fn serialization_round_trips_explicit_agent_selection() {
        let mut raw = serde_json::to_value(AppSettings::default()).expect("serialize defaults");
        raw["default_cli_agent"] = Value::String("codex".to_string());
        let value = round_trip(raw);

        assert_eq!(
            agent_from(&value),
            Some("codex"),
            "saving and loading should preserve an explicit default agent"
        );
    }

    #[test]
    fn invalid_agent_value_resets_to_default() {
        let mut raw = serde_json::to_value(AppSettings::default()).expect("serialize defaults");
        raw["default_cli_agent"] = Value::String("madeup".to_string());
        let value = round_trip(raw);

        assert_eq!(
            agent_from(&value),
            Some("claude"),
            "invalid agent strings should fall back to the standard default"
        );
    }

    #[test]
    fn optional_projects_folder_and_agent_round_trip() {
        let mut settings = AppSettings::default();
        settings.projects_folder = Some("/tmp/projects".to_string());
        settings.default_cli_agent = "gemini".to_string();

        let serialized = serde_json::to_value(&settings).expect("serialize settings");
        let deserialized: AppSettings =
            serde_json::from_value(serialized).expect("deserialize settings");

        assert_eq!(
            deserialized.projects_folder,
            Some("/tmp/projects".to_string()),
            "projects_folder should round-trip through serde"
        );
        assert_eq!(
            deserialized.default_cli_agent,
            "gemini".to_string(),
            "default_cli_agent should retain explicit choices"
        );
    }

    // --- Regression: "autohand" must be accepted as a valid default agent ---

    #[test]
    fn autohand_agent_survives_deserialization_round_trip() {
        let mut raw = serde_json::to_value(AppSettings::default()).expect("serialize defaults");
        raw["default_cli_agent"] = Value::String("autohand".to_string());
        let value = round_trip(raw);

        assert_eq!(
            agent_from(&value),
            Some("autohand"),
            "\"autohand\" is a valid agent and must not be replaced by the default"
        );
    }

    #[test]
    fn autohand_agent_survives_normalize() {
        let mut settings = AppSettings::default();
        settings.default_cli_agent = "autohand".to_string();
        settings.normalize();

        assert_eq!(
            settings.default_cli_agent, "autohand",
            "normalize() must keep \"autohand\" intact — it is in the allowlist"
        );
    }

    #[test]
    fn default_settings_include_robot_status_defaults() {
        let value = serde_json::to_value(AppSettings::default()).expect("serialize defaults");
        let robot_settings = value
            .get("robot_settings")
            .expect("robot_settings should be present on default settings");

        assert_eq!(
            robot_settings
                .get("live_status_enabled")
                .and_then(|v| v.as_bool()),
            Some(false),
            "live status should default to disabled for the robot workbench"
        );
        assert_eq!(
            robot_settings
                .get("mujoco_live_status_enabled")
                .and_then(|v| v.as_bool()),
            Some(false),
            "MuJoCo polling should default to disabled"
        );
        assert_eq!(
            robot_settings.get("mujoco_viewer_url"),
            None,
            "obsolete MuJoCo web viewer URLs should no longer be part of default settings"
        );
        assert_eq!(
            robot_settings.get("mujoco_viewer_launch_command"),
            None,
            "obsolete MuJoCo web viewer launch commands should no longer be part of default settings"
        );
        assert_eq!(
            robot_settings
                .get("daemon_base_url")
                .and_then(|v| v.as_str()),
            Some("http://localhost:8000"),
            "the default daemon URL should point at the local Reachy daemon"
        );
    }

    #[test]
    fn blank_robot_daemon_url_resets_to_default() {
        let mut raw = serde_json::to_value(AppSettings::default()).expect("serialize defaults");
        raw["robot_settings"]["daemon_base_url"] = Value::String("   ".to_string());
        let value = round_trip(raw);

        let daemon_url = value
            .get("robot_settings")
            .and_then(|v| v.get("daemon_base_url"))
            .and_then(|v| v.as_str());

        assert_eq!(
            daemon_url,
            Some("http://localhost:8000"),
            "blank daemon URLs should normalize back to the standard default"
        );
    }

    #[test]
    fn legacy_mujoco_viewer_fields_are_dropped_on_round_trip() {
        let mut raw = serde_json::to_value(AppSettings::default()).expect("serialize defaults");
        raw["robot_settings"]["mujoco_viewer_url"] =
            Value::String("http://127.0.0.1:9001".to_string());
        raw["robot_settings"]["mujoco_viewer_launch_command"] =
            Value::String("conda run -n reachy python -m viewer".to_string());
        let value = round_trip(raw);

        let robot_settings = value
            .get("robot_settings")
            .expect("robot_settings should still serialize");

        assert_eq!(
            robot_settings.get("mujoco_viewer_url"),
            None,
            "legacy viewer URLs should be ignored on load and omitted on save"
        );
        assert_eq!(
            robot_settings.get("mujoco_viewer_launch_command"),
            None,
            "legacy viewer launch commands should be ignored on load and omitted on save"
        );
    }
}
