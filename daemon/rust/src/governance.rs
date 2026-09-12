use crate::{Error, Result, types::{Action, Event, Request, now_ms}};
use serde::{Deserialize, Serialize};
use std::{collections::{HashMap, VecDeque}, sync::{Arc, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};
use tokio_util::sync::CancellationToken;

pub const MAX_REQUEST_TTL_MS: u64 = 30_000;
pub const MAX_CAPABILITY_TTL_MS: u64 = 300_000;

#[derive(Clone, Default)]
pub struct EmergencyLatch {
    stopped: Arc<AtomicBool>,
    token: CancellationToken,
}
impl EmergencyLatch {
    pub fn stop(&self) { self.stopped.store(true, Ordering::SeqCst); self.token.cancel(); }
    pub fn check(&self) -> Result<()> { if self.stopped.load(Ordering::SeqCst) { Err(Error::Emergency) } else { Ok(()) } }
    pub fn token(&self) -> CancellationToken { self.token.clone() }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Scope { Observe { display_id: u32 }, Input { display_id: u32 }, Process { executable_id: String } }

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Capability {
    pub id: String,
    pub peer_sha256: String,
    pub expires_at_ms: u64,
    pub scope: Scope,
}

pub struct Policy {
    capabilities: HashMap<String, (Capability, Instant)>,
    pub simulation: bool,
}
impl Policy {
    pub fn new(simulation: bool, capabilities: Vec<Capability>, now: u64) -> Result<Self> {
        if capabilities.len() > 128 { return Err(Error::Limit("capabilities")); }
        let mut grants = HashMap::new();
        for cap in capabilities {
            if cap.id.is_empty() || cap.id.len() > 128 || cap.peer_sha256.len() != 64 || !cap.peer_sha256.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) {
                return Err(Error::Denied("invalid capability identity"));
            }
            let remaining = cap.expires_at_ms.checked_sub(now).filter(|v| *v > 0 && *v <= MAX_CAPABILITY_TTL_MS).ok_or(Error::Stale)?;
            let expiry = Instant::now() + Duration::from_millis(remaining);
            if grants.insert(cap.id.clone(), (cap, expiry)).is_some() { return Err(Error::Denied("duplicate capability")); }
        }
        Ok(Self { capabilities: grants, simulation })
    }

    /// Checked again at dispatch time; queue wait never extends authority.
    pub fn authorize(&self, peer: &str, request: &Request, now: u64, latch: &EmergencyLatch) -> Result<()> {
        if request.expires_at_ms <= now { return Err(Error::Stale); }
        if matches!(request.action, Action::Status | Action::EmergencyStop) { return Ok(()); }
        latch.check()?;
        let id = request.capability_id.as_ref().ok_or(Error::Denied("capability required"))?;
        let (cap, monotonic_expiry) = self.capabilities.get(id).ok_or(Error::Denied("unknown capability"))?;
        if cap.peer_sha256 != peer { return Err(Error::Denied("capability peer mismatch")); }
        if cap.expires_at_ms <= now || Instant::now() >= *monotonic_expiry { return Err(Error::Stale); }
        let requested = match &request.action {
            Action::Capture { display_id } => Scope::Observe { display_id: *display_id },
            Action::Click { display_id, .. } => Scope::Input { display_id: *display_id },
            Action::RunProcess { executable_id, .. } => Scope::Process { executable_id: executable_id.clone() },
            _ => unreachable!(),
        };
        if cap.scope != requested { return Err(Error::Denied("scope mismatch")); }
        Ok(())
    }

    pub fn action_deadline_ms(&self, request: &Request) -> u64 {
        request.capability_id.as_ref().and_then(|id| self.capabilities.get(id)).map(|(c, _)| c.expires_at_ms.min(request.expires_at_ms)).unwrap_or(request.expires_at_ms)
    }
}

/// Bounded in-memory event journal. Durable/tamper-evident retention belongs to a collector.
pub struct EventJournal { entries: VecDeque<Event>, capacity: usize, next: u64 }
impl EventJournal {
    pub fn new(capacity: usize) -> Result<Self> { if capacity == 0 || capacity > 4096 { return Err(Error::Limit("event journal")); } Ok(Self { entries: VecDeque::new(), capacity, next: 0 }) }
    pub fn record(&mut self, request_id: uuid::Uuid, phase: &'static str, simulation: bool) {
        self.next = self.next.saturating_add(1);
        if self.entries.len() == self.capacity { self.entries.pop_front(); }
        self.entries.push_back(Event { sequence: self.next, at_ms: now_ms(), request_id, phase, simulation });
    }
    pub fn entries(&self) -> &VecDeque<Event> { &self.entries }
}
