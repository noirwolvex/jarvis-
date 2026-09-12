use crate::{
    Error, Result,
    capture::{CaptureRing, ScreenCapture, SimulationCapture},
    governance::{EmergencyLatch, EventJournal, Policy},
    input::{InputController, SimulationInput},
    process::ProcessManager,
    types::{Action, Reply, Request, now_ms},
};
use serde_json::json;
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

pub const DISPATCH_CAPACITY: usize = 32;
pub const REPLY_CAPACITY: usize = 32;
pub const MAX_FRAME_BYTES: usize = 32 * 1024 * 1024;

pub struct Job {
    pub peer: String,
    pub request: Request,
    pub disconnected: CancellationToken,
    pub reply: mpsc::Sender<Reply>,
    pub deadline: Instant,
}
#[derive(Clone)]
pub struct Dispatcher {
    sender: mpsc::Sender<Job>,
    pub emergency: EmergencyLatch,
    pub simulation: bool,
}
impl Dispatcher {
    pub fn start(policy: Policy, processes: ProcessManager, native_capture: bool) -> Result<Self> {
        let capture: Arc<dyn ScreenCapture> = if native_capture {
            #[cfg(feature = "native")]
            {
                Arc::new(crate::capture::NativeCapture)
            }
            #[cfg(not(feature = "native"))]
            {
                return Err(Error::Unsupported(
                    "compile with native feature for capture",
                ));
            }
        } else {
            Arc::new(SimulationCapture)
        };
        let (sender, receiver) = mpsc::channel(DISPATCH_CAPACITY);
        let emergency = EmergencyLatch::default();
        let simulation = policy.simulation;
        let worker = Worker {
            policy,
            processes,
            capture,
            ring: CaptureRing::new(4, 64 * 1024 * 1024)?,
            journal: EventJournal::new(1024)?,
            emergency: emergency.clone(),
        };
        tokio::spawn(worker.run(receiver));
        Ok(Self {
            sender,
            emergency,
            simulation,
        })
    }
    pub fn submit(&self, job: Job) -> Result<()> {
        if !matches!(job.request.action, Action::Status {}) {
            self.emergency.check()?;
        }
        self.sender
            .try_send(job)
            .map_err(|_| Error::Limit("dispatch queue full or closed"))
    }
}

struct Worker {
    policy: Policy,
    processes: ProcessManager,
    capture: Arc<dyn ScreenCapture>,
    ring: CaptureRing,
    journal: EventJournal,
    emergency: EmergencyLatch,
}
impl Worker {
    async fn run(mut self, mut receiver: mpsc::Receiver<Job>) {
        while let Some(job) = receiver.recv().await {
            if job.disconnected.is_cancelled() || job.reply.is_closed() {
                continue;
            }
            let id = job.request.request_id;
            self.journal.record(id, "received", self.policy.simulation);
            let result = self.execute(&job).await;
            self.journal.record(
                id,
                if result.is_ok() {
                    "completed"
                } else {
                    "denied_or_failed"
                },
                self.policy.simulation,
            );
            let _ = tokio::time::timeout(
                Duration::from_secs(2),
                job.reply.send(Reply::result(id, result)),
            )
            .await;
        }
    }
    fn authorize(&self, job: &Job) -> Result<()> {
        if Instant::now() >= job.deadline {
            return Err(Error::Stale);
        }
        if job.disconnected.is_cancelled() {
            return Err(Error::Denied("client disconnected"));
        }
        self.policy
            .authorize(&job.peer, &job.request, now_ms(), &self.emergency)
    }
    async fn execute(&mut self, job: &Job) -> Result<serde_json::Value> {
        self.authorize(job)?;
        match &job.request.action {
            Action::Status {} => Ok(
                json!({"simulation": self.policy.simulation, "emergency_stopped": self.emergency.check().is_err(), "capture_ring_frames": self.ring.len(), "capture_ring_bytes": self.ring.bytes(), "audit_events_retained": self.journal.entries().len(), "native_input": "disabled_pending_foreground_binding", "accessibility": "unsupported", "sandbox": "none"}),
            ),
            Action::EmergencyStop {} => {
                self.emergency.stop();
                Ok(json!({"emergency_stopped": true}))
            }
            Action::Capture { display_id } => {
                let capture = self.capture.clone();
                let display_id = *display_id;
                // At most one native capture runs. Do not abandon a blocking OS call
                // and spawn replacements: that would defeat the concurrency bound.
                let frame = tokio::task::spawn_blocking(move || {
                    capture.capture(display_id, MAX_FRAME_BYTES)
                })
                .await
                .map_err(|_| Error::Operation("capture worker failed".into()))??;
                self.authorize(job)?;
                let frame = self.ring.push(frame)?;
                Ok(json!({"frame": frame, "pixels_transport": "metadata_only"}))
            }
            Action::Click {
                display_id,
                frame_id,
                x,
                y,
            } => {
                let frame = self.ring.get(*frame_id)?;
                if frame.display.id != *display_id {
                    return Err(Error::Denied("frame display mismatch"));
                }
                if !self.policy.simulation {
                    return Err(Error::Unsupported(
                        "native input requires foreground window and element binding",
                    ));
                }
                self.authorize(job)?;
                SimulationInput.click(frame, *x, *y, &self.emergency)?;
                Ok(
                    json!({"simulation": true, "executed": false, "verified": false, "planned_click": {"x": x, "y": y}, "verification": "requires_independent_postcondition_evidence"}),
                )
            }
            Action::RunProcess {
                executable_id,
                args,
                timeout_ms,
            } => {
                self.processes.validate(executable_id, args, *timeout_ms)?;
                if self.policy.simulation {
                    return Ok(
                        json!({"simulation": true, "executed": false, "verified": false, "executable_id": executable_id}),
                    );
                }
                let remaining = self
                    .policy
                    .action_deadline_ms(&job.request)
                    .saturating_sub(now_ms())
                    .min(
                        job.deadline
                            .saturating_duration_since(Instant::now())
                            .as_millis() as u64,
                    );
                if remaining == 0 {
                    return Err(Error::Stale);
                }
                let result = self
                    .processes
                    .run(
                        executable_id,
                        args,
                        (*timeout_ms).min(remaining),
                        job.request.request_id,
                        self.emergency.clone(),
                        job.disconnected.clone(),
                        job.reply.clone(),
                    )
                    .await?;
                Ok(serde_json::to_value(result)?)
            }
        }
    }
}
