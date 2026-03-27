#[cfg(test)]
mod tests {
    #[test]
    fn legacy_agent_status_monitor_commands_are_removed() {
        let llm_commands = include_str!("../../commands/llm_commands.rs");
        let lib_rs = include_str!("../../lib.rs");

        assert!(
            !llm_commands.contains("pub async fn check_ai_agents"),
            "legacy check_ai_agents command should stay removed once the old status chain is retired"
        );
        assert!(
            !llm_commands.contains("pub async fn monitor_ai_agents"),
            "legacy monitor_ai_agents command should stay removed once the old status chain is retired"
        );
        assert!(
            !llm_commands.contains("fn start_agent_monitor"),
            "background agent status monitor should stay removed from llm_commands"
        );
        assert!(
            !llm_commands.contains("app.emit(\"ai-agent-status\""),
            "llm_commands should no longer emit ai-agent-status events after trimming the old footer chain"
        );
        assert!(
            !lib_rs.contains("check_ai_agents,"),
            "Tauri invoke handler should not register the removed check_ai_agents command"
        );
        assert!(
            !lib_rs.contains("monitor_ai_agents,"),
            "Tauri invoke handler should not register the removed monitor_ai_agents command"
        );
    }
}
