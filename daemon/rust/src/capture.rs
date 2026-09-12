use crate::{Error, Result, types::now_ms};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    sync::Arc,
    time::{Duration, Instant},
};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize)]
pub struct Display {
    pub id: u32,
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub scale: f32,
}
#[derive(Debug, Clone, Serialize)]
pub struct Frame {
    pub id: Uuid,
    pub display: Display,
    pub captured_at_ms: u64,
    pub sha256: String,
    pub simulation: bool,
    #[serde(skip)]
    pub rgba: Arc<[u8]>,
    #[serde(skip)]
    pub captured_at: Instant,
}
impl Frame {
    pub fn new(
        display: Display,
        pixels: Vec<u8>,
        simulation: bool,
        max_bytes: usize,
    ) -> Result<Self> {
        let expected = (display.width as usize)
            .checked_mul(display.height as usize)
            .and_then(|v| v.checked_mul(4))
            .ok_or(Error::Limit("frame dimensions"))?;
        if expected == 0
            || expected > max_bytes
            || pixels.len() != expected
            || !display.scale.is_finite()
            || display.scale <= 0.0
        {
            return Err(Error::Limit("frame bytes or scale"));
        }
        Ok(Self {
            id: Uuid::new_v4(),
            display,
            captured_at_ms: now_ms(),
            captured_at: Instant::now(),
            sha256: format!("{:x}", Sha256::digest(&pixels)),
            simulation,
            rgba: pixels.into(),
        })
    }
    pub fn fresh(&self, now: Instant, ttl: Duration) -> bool {
        now.checked_duration_since(self.captured_at)
            .is_some_and(|age| age < ttl)
    }
    pub fn contains(&self, x: i32, y: i32) -> bool {
        let dx = i64::from(x) - i64::from(self.display.x);
        let dy = i64::from(y) - i64::from(self.display.y);
        dx >= 0
            && dy >= 0
            && dx < i64::from(self.display.width)
            && dy < i64::from(self.display.height)
    }
}

pub trait ScreenCapture: Send + Sync {
    fn displays(&self) -> Result<Vec<Display>>;
    fn capture(&self, display_id: u32, max_bytes: usize) -> Result<Frame>;
    fn capture_window(&self, _window_id: u32, _max_bytes: usize) -> Result<Frame> {
        Err(Error::Unsupported("window capture adapter not implemented"))
    }
}
pub struct SimulationCapture;
impl ScreenCapture for SimulationCapture {
    fn displays(&self) -> Result<Vec<Display>> {
        Ok(vec![Display {
            id: 0,
            x: 0,
            y: 0,
            width: 64,
            height: 64,
            scale: 1.0,
        }])
    }
    fn capture(&self, display_id: u32, max_bytes: usize) -> Result<Frame> {
        let display = self
            .displays()?
            .into_iter()
            .find(|d| d.id == display_id)
            .ok_or(Error::Denied("unknown display"))?;
        Frame::new(display, vec![0; 64 * 64 * 4], true, max_bytes)
    }
}

/// Feature-gated real monitor adapter; xcap supplies Windows and Linux implementations.
#[cfg(feature = "native")]
pub struct NativeCapture;
#[cfg(feature = "native")]
impl ScreenCapture for NativeCapture {
    fn displays(&self) -> Result<Vec<Display>> {
        xcap::Monitor::all()
            .map_err(native_error)?
            .iter()
            .map(native_display)
            .collect()
    }
    fn capture(&self, display_id: u32, max_bytes: usize) -> Result<Frame> {
        let monitor = xcap::Monitor::all()
            .map_err(native_error)?
            .into_iter()
            .find(|m| m.id().ok() == Some(display_id))
            .ok_or(Error::Denied("unknown display"))?;
        let display = native_display(&monitor)?;
        let bytes = u64::from(display.width) * u64::from(display.height) * 4;
        if bytes > max_bytes as u64 {
            return Err(Error::Limit("native frame bytes"));
        }
        let image = monitor.capture_image().map_err(native_error)?;
        if image.width() != display.width || image.height() != display.height {
            return Err(Error::Stale);
        }
        Frame::new(display, image.into_raw(), false, max_bytes)
    }
}
#[cfg(feature = "native")]
fn native_error(_error: impl std::fmt::Display) -> Error {
    Error::Operation("native capture backend failed".into())
}
#[cfg(feature = "native")]
fn native_display(m: &xcap::Monitor) -> Result<Display> {
    Ok(Display {
        id: m.id().map_err(native_error)?,
        x: m.x().map_err(native_error)?,
        y: m.y().map_err(native_error)?,
        width: m.width().map_err(native_error)?,
        height: m.height().map_err(native_error)?,
        scale: m.scale_factor().map_err(native_error)?,
    })
}

/// Count and byte bounded; identical frames share their pixel allocation.
pub struct CaptureRing {
    frames: VecDeque<Frame>,
    max_count: usize,
    max_bytes: usize,
    bytes: usize,
}
impl CaptureRing {
    pub fn new(max_count: usize, max_bytes: usize) -> Result<Self> {
        if max_count == 0 || max_count > 64 || max_bytes == 0 || max_bytes > 256 * 1024 * 1024 {
            return Err(Error::Limit("capture ring configuration"));
        }
        Ok(Self {
            frames: VecDeque::new(),
            max_count,
            max_bytes,
            bytes: 0,
        })
    }
    pub fn push(&mut self, mut frame: Frame) -> Result<Frame> {
        if frame.rgba.len() > self.max_bytes {
            return Err(Error::Limit("capture ring frame"));
        }
        if let Some(previous) = self.frames.iter().rev().find(|p| {
            p.display.id == frame.display.id
                && p.sha256 == frame.sha256
                && p.rgba.len() == frame.rgba.len()
        }) {
            frame.rgba = previous.rgba.clone();
        }
        while self.frames.len() >= self.max_count || self.bytes + frame.rgba.len() > self.max_bytes
        {
            if let Some(old) = self.frames.pop_front() {
                self.bytes -= old.rgba.len();
            }
        }
        self.bytes += frame.rgba.len();
        self.frames.push_back(frame.clone());
        Ok(frame)
    }
    pub fn get(&self, id: Uuid) -> Result<&Frame> {
        self.frames.iter().find(|f| f.id == id).ok_or(Error::Stale)
    }
    pub fn len(&self) -> usize {
        self.frames.len()
    }
    pub fn is_empty(&self) -> bool {
        self.frames.is_empty()
    }
    pub fn bytes(&self) -> usize {
        self.bytes
    }
}

#[derive(Debug, Serialize)]
pub struct AccessibilityNode {
    pub id: String,
    pub role: String,
    pub name: String,
}
pub trait AccessibilityProvider: Send + Sync {
    fn tree(&self) -> Result<Vec<AccessibilityNode>>;
}
pub struct UnsupportedAccessibility;
impl AccessibilityProvider for UnsupportedAccessibility {
    fn tree(&self) -> Result<Vec<AccessibilityNode>> {
        Err(Error::Unsupported(
            "Windows UI Automation and Linux AT-SPI bridges are not implemented",
        ))
    }
}

pub fn verify_changed(before: &Frame, after: &Frame) -> Result<bool> {
    if before.display.id != after.display.id
        || after.captured_at <= before.captured_at
        || !after.fresh(Instant::now(), Duration::from_secs(2))
    {
        return Err(Error::Stale);
    }
    Ok(before.sha256 != after.sha256)
}
