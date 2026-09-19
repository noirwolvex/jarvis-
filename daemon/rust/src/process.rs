use crate::{Error, Result, governance::EmergencyLatch, types::Reply};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt},
    process::Command,
    sync::mpsc,
};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

pub const MAX_OUTPUT_BYTES: usize = 16 * 1024;
pub const MAX_PROCESS_MS: u64 = 30_000;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutableRule {
    pub id: String,
    pub path: PathBuf,
    pub sha256: String,
    pub working_directory: PathBuf,
    /// Exact argv vectors. No patterns, expansion, shell syntax or implicit PATH.
    pub allowed_argv: Vec<Vec<String>>,
}
pub struct ProcessManager {
    rules: HashMap<String, ExecutableRule>,
    allow_uncontained: bool,
}

#[derive(Debug, Serialize)]
pub struct ProcessOutcome {
    pub exit_code: Option<i32>,
    pub verified: bool,
    pub termination: &'static str,
    pub output_bytes: usize,
    pub containment: &'static str,
}

impl ProcessManager {
    pub fn new(rules: Vec<ExecutableRule>, allow_uncontained: bool) -> Result<Self> {
        if rules.len() > 32 {
            return Err(Error::Limit("executable rules"));
        }
        let mut map = HashMap::new();
        for mut rule in rules {
            if rule.id.is_empty()
                || rule.id.len() > 128
                || !rule.path.is_absolute()
                || !rule.working_directory.is_absolute()
                || rule.allowed_argv.is_empty()
                || rule.allowed_argv.len() > 32
                || rule.sha256.len() != 64
            {
                return Err(Error::Denied("invalid executable rule"));
            }
            for args in &rule.allowed_argv {
                validate_argv(args)?;
            }
            let name = rule
                .path
                .file_stem()
                .and_then(|x| x.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if [
                "cmd",
                "powershell",
                "pwsh",
                "sh",
                "bash",
                "zsh",
                "dash",
                "python",
                "python3",
                "node",
                "ruby",
                "perl",
                "wscript",
                "cscript",
                "mshta",
            ]
            .contains(&name.as_str())
            {
                return Err(Error::Denied(
                    "shells and interpreters cannot be allowlisted",
                ));
            }
            rule.path = rule.path.canonicalize()?;
            rule.working_directory = rule.working_directory.canonicalize()?;
            if !rule.path.is_file() || !rule.working_directory.is_dir() {
                return Err(Error::Denied("executable or working directory invalid"));
            }
            if map.insert(rule.id.clone(), rule).is_some() {
                return Err(Error::Denied("duplicate executable rule"));
            }
        }
        Ok(Self {
            rules: map,
            allow_uncontained,
        })
    }
    pub fn validate(&self, id: &str, args: &[String], timeout_ms: u64) -> Result<&ExecutableRule> {
        validate_argv(args)?;
        if timeout_ms == 0 || timeout_ms > MAX_PROCESS_MS {
            return Err(Error::Limit("process timeout"));
        }
        let rule = self
            .rules
            .get(id)
            .ok_or(Error::Denied("executable not allowlisted"))?;
        if !rule.allowed_argv.iter().any(|approved| approved == args) {
            return Err(Error::Denied("argv not allowlisted"));
        }
        Ok(rule)
    }

    #[expect(
        clippy::too_many_arguments,
        reason = "Preserve explicit process authority, cancellation and output channels at this boundary"
    )]
    pub async fn run(
        &self,
        id: &str,
        args: &[String],
        timeout_ms: u64,
        request_id: Uuid,
        emergency: EmergencyLatch,
        disconnected: CancellationToken,
        output: mpsc::Sender<Reply>,
    ) -> Result<ProcessOutcome> {
        let rule = self.validate(id, args, timeout_ms)?;
        if !self.allow_uncontained {
            return Err(Error::Unsupported(
                "OS process containment unavailable; explicit allow_uncontained_processes required",
            ));
        }
        emergency.check()?;
        if disconnected.is_cancelled() {
            return Err(Error::Denied("client disconnected"));
        }
        // Hash before spawning; administrators must protect the executable directory
        // from concurrent replacement. This is not an atomic OS execution binding.
        if file_sha256(&rule.path)? != rule.sha256 {
            return Err(Error::Denied("executable digest mismatch"));
        }
        emergency.check()?;
        let mut command = Command::new(&rule.path);
        command
            .args(args)
            .current_dir(&rule.working_directory)
            .env_clear()
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        {
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn()?;
        let stdout = child
            .stdout
            .take()
            .ok_or(Error::Operation("stdout unavailable".into()))?;
        let stderr = child
            .stderr
            .take()
            .ok_or(Error::Operation("stderr unavailable".into()))?;
        let bytes = Arc::new(AtomicUsize::new(0));
        let overflow = CancellationToken::new();
        let stdout_task = tokio::spawn(pump(
            stdout,
            "stdout",
            request_id,
            bytes.clone(),
            overflow.clone(),
            output.clone(),
        ));
        let stderr_task = tokio::spawn(pump(
            stderr,
            "stderr",
            request_id,
            bytes.clone(),
            overflow.clone(),
            output,
        ));
        let emergency_token = emergency.token();
        let (status, mut reason) = tokio::select! {
            biased;
            _ = emergency_token.cancelled() => (None, "emergency_stop"),
            _ = disconnected.cancelled() => (None, "client_disconnected"),
            _ = overflow.cancelled() => (None, "output_limit"),
            _ = tokio::time::sleep(Duration::from_millis(timeout_ms)) => (None, "timeout"),
            status = child.wait() => (Some(status?), "exited"),
        };
        if status.is_none() {
            // Kills/reaps the direct child only; descendants require an OS sandbox.
            child.start_kill()?;
            let _ = tokio::time::timeout(Duration::from_secs(2), child.wait()).await;
        }
        // Descendants may inherit pipe handles. Never wait forever for EOF.
        let mut stdout_task = stdout_task;
        let mut stderr_task = stderr_task;
        let drained = tokio::time::timeout(Duration::from_millis(500), async {
            let _ = (&mut stdout_task).await;
            let _ = (&mut stderr_task).await;
        })
        .await
        .is_ok();
        stdout_task.abort();
        stderr_task.abort();
        if overflow.is_cancelled() && reason == "exited" {
            reason = "output_limit";
        }
        if !drained && reason == "exited" {
            reason = "pipe_drain_timeout";
        }
        Ok(ProcessOutcome {
            exit_code: status.and_then(|s| s.code()),
            verified: reason == "exited" && status.is_some_and(|s| s.success()),
            termination: reason,
            output_bytes: bytes.load(Ordering::SeqCst).min(MAX_OUTPUT_BYTES),
            containment: "direct_child_only_no_os_sandbox",
        })
    }
}

pub fn validate_argv(args: &[String]) -> Result<()> {
    if args.len() > 32
        || args.iter().any(|a| a.len() > 4096 || a.contains('\0'))
        || args.iter().map(String::len).sum::<usize>() > 8192
    {
        return Err(Error::Limit("argv size"));
    }
    Ok(())
}

pub fn file_sha256(path: &Path) -> Result<String> {
    use std::io::Read;
    let mut file = std::fs::File::open(path)?;
    if file.metadata()?.len() > 256 * 1024 * 1024 {
        return Err(Error::Limit("executable bytes"));
    }
    let mut digest = Sha256::new();
    let mut buffer = [0; 8192];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

async fn pump<R: AsyncRead + Unpin>(
    mut reader: R,
    stream: &'static str,
    request_id: Uuid,
    bytes: Arc<AtomicUsize>,
    overflow: CancellationToken,
    output: mpsc::Sender<Reply>,
) {
    let mut buffer = [0; 1024];
    loop {
        let count = match reader.read(&mut buffer).await {
            Ok(0) => return,
            Ok(n) => n,
            Err(_) => {
                overflow.cancel();
                return;
            }
        };
        let prior = bytes.fetch_add(count, Ordering::SeqCst);
        if prior.saturating_add(count) > MAX_OUTPUT_BYTES {
            overflow.cancel();
            return;
        }
        // Chunks are lossy UTF-8 independently; a consumer must not treat them as
        // terminal commands. ANSI/control escaping is the UI's responsibility.
        let event = Reply::Output {
            request_id,
            stream: stream.into(),
            text: String::from_utf8_lossy(&buffer[..count]).into_owned(),
        };
        if output.try_send(event).is_err() {
            overflow.cancel();
            return;
        }
    }
}
