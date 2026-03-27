use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::AtomicU64;
use std::sync::atomic::Ordering;

use async_trait::async_trait;
use serde_json::Value as JsonValue;
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;

use crate::runtime::ExecuteRequest;
use crate::runtime::RuntimeResponse;
use crate::runtime::WaitRequest;

const DISABLED_MESSAGE: &str =
    "exec is unavailable in this build because code mode was compiled without V8";

#[async_trait]
pub trait CodeModeTurnHost: Send + Sync {
    async fn invoke_tool(
        &self,
        tool_name: String,
        input: Option<JsonValue>,
        cancellation_token: CancellationToken,
    ) -> Result<JsonValue, String>;

    async fn notify(&self, call_id: String, cell_id: String, text: String) -> Result<(), String>;
}

pub struct CodeModeService {
    stored_values: Arc<Mutex<HashMap<String, JsonValue>>>,
    next_cell_id: AtomicU64,
}

impl CodeModeService {
    pub fn new() -> Self {
        Self {
            stored_values: Arc::new(Mutex::new(HashMap::new())),
            next_cell_id: AtomicU64::new(1),
        }
    }

    pub async fn stored_values(&self) -> HashMap<String, JsonValue> {
        self.stored_values.lock().await.clone()
    }

    pub async fn replace_stored_values(&self, values: HashMap<String, JsonValue>) {
        *self.stored_values.lock().await = values;
    }

    pub async fn execute(&self, request: ExecuteRequest) -> Result<RuntimeResponse, String> {
        let cell_id = self
            .next_cell_id
            .fetch_add(1, Ordering::Relaxed)
            .to_string();

        Ok(RuntimeResponse::Result {
            cell_id,
            content_items: Vec::new(),
            stored_values: request.stored_values,
            error_text: Some(format!(
                "{DISABLED_MESSAGE}. Source length: {} characters.",
                request.source.chars().count()
            )),
        })
    }

    pub async fn wait(&self, request: WaitRequest) -> Result<RuntimeResponse, String> {
        Ok(RuntimeResponse::Result {
            cell_id: request.cell_id,
            content_items: Vec::new(),
            stored_values: self.stored_values.lock().await.clone(),
            error_text: Some(DISABLED_MESSAGE.to_string()),
        })
    }

    pub fn start_turn_worker(&self, _host: Arc<dyn CodeModeTurnHost>) -> CodeModeTurnWorker {
        CodeModeTurnWorker {}
    }
}

impl Default for CodeModeService {
    fn default() -> Self {
        Self::new()
    }
}

pub struct CodeModeTurnWorker {}
