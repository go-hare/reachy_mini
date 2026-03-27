use anyhow::anyhow;
use clap::Parser;
use codex_arg0::Arg0DispatchPaths;
use codex_arg0::arg0_dispatch_or_else;
use codex_tui_app_server::Cli;
use codex_tui_app_server::run_main;
use codex_utils_cli::CliConfigOverrides;

#[derive(Parser, Debug)]
struct TopCli {
    #[clap(flatten)]
    config_overrides: CliConfigOverrides,

    #[clap(flatten)]
    inner: Cli,

    /// Remote websocket endpoint for the Python backend.
    #[arg(long = "remote", default_value = "ws://127.0.0.1:4500")]
    remote: String,

    /// Name of the environment variable that stores the bearer token for the remote backend.
    #[arg(long = "remote-auth-token-env", value_name = "ENV_VAR")]
    remote_auth_token_env: Option<String>,
}

fn read_remote_auth_token(env_var: &str) -> anyhow::Result<String> {
    let value = std::env::var(env_var)
        .map_err(|_| anyhow!("environment variable `{env_var}` is not set"))?;
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(anyhow!("environment variable `{env_var}` is empty"));
    }
    Ok(trimmed.to_owned())
}

fn main() -> anyhow::Result<()> {
    arg0_dispatch_or_else(|arg0_paths: Arg0DispatchPaths| async move {
        let top_cli = TopCli::parse();
        let mut inner = top_cli.inner;
        inner
            .config_overrides
            .raw_overrides
            .splice(0..0, top_cli.config_overrides.raw_overrides);

        let remote_auth_token = top_cli
            .remote_auth_token_env
            .as_deref()
            .map(read_remote_auth_token)
            .transpose()?;

        let _exit_info = run_main(
            inner,
            arg0_paths,
            codex_core::config_loader::LoaderOverrides::default(),
            Some(top_cli.remote),
            remote_auth_token,
        )
        .await?;

        Ok(())
    })
}
