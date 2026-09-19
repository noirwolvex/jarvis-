use serde::{Deserialize, Serialize};
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("denied: {0}")]
    Denied(&'static str),
    #[error("unsupported: {0}")]
    Unsupported(&'static str),
    #[error("resource limit: {0}")]
    Limit(&'static str),
    #[error("emergency stop latched; local restart required")]
    Emergency,
    #[error("expired or stale")]
    Stale,
    #[error("protocol error: {0}")]
    Protocol(&'static str),
    #[error("operation failed: {0}")]
    Operation(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ForegroundBinding {
    pub hwnd: u64,
    pub process_id: u32,
    pub title: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MouseButton {
    Left,
    Right,
    Middle,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    Status {},
    Capture {
        display_id: u32,
    },
    Click {
        display_id: u32,
        frame_id: Uuid,
        x: i32,
        y: i32,
        foreground: ForegroundBinding,
    },
    ClickButton {
        display_id: u32,
        frame_id: Uuid,
        x: i32,
        y: i32,
        button: MouseButton,
        clicks: u8,
        foreground: ForegroundBinding,
    },
    PointerMove {
        display_id: u32,
        frame_id: Uuid,
        x: i32,
        y: i32,
        #[serde(default)]
        duration_ms: u64,
        foreground: ForegroundBinding,
    },
    Drag {
        display_id: u32,
        frame_id: Uuid,
        start_x: i32,
        start_y: i32,
        end_x: i32,
        end_y: i32,
        duration_ms: u64,
        button: MouseButton,
        foreground: ForegroundBinding,
    },
    Scroll {
        display_id: u32,
        frame_id: Uuid,
        clicks: i32,
        foreground: ForegroundBinding,
    },
    PressKey {
        display_id: u32,
        frame_id: Uuid,
        key: String,
        foreground: ForegroundBinding,
    },
    Hotkey {
        display_id: u32,
        frame_id: Uuid,
        keys: Vec<String>,
        foreground: ForegroundBinding,
    },
    TypeText {
        display_id: u32,
        frame_id: Uuid,
        text: String,
        foreground: ForegroundBinding,
    },
    RunProcess {
        executable_id: String,
        args: Vec<String>,
        timeout_ms: u64,
    },
    EmergencyStop {},
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub protocol: u8,
    pub session: Uuid,
    pub seq: u64,
    pub expires_at_ms: u64,
    pub request_id: Uuid,
    pub capability_id: Option<String>,
    pub action: Action,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Reply {
    Hello {
        protocol: u8,
        session: Uuid,
        max_frame_bytes: usize,
        simulation: bool,
    },
    Output {
        request_id: Uuid,
        stream: String,
        text: String,
    },
    Result {
        request_id: Uuid,
        ok: bool,
        data: serde_json::Value,
    },
}

impl Reply {
    pub fn result(id: Uuid, result: Result<serde_json::Value>) -> Self {
        match result {
            Ok(data) => Self::Result {
                request_id: id,
                ok: true,
                data,
            },
            // No request payload, credential or environment data is reflected on errors.
            Err(error) => Self::Result {
                request_id: id,
                ok: false,
                data: serde_json::json!({"error": error.to_string()}),
            },
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Event {
    pub sequence: u64,
    pub at_ms: u64,
    pub request_id: Uuid,
    pub phase: &'static str,
    pub simulation: bool,
}
