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

#[derive(Debug, Clone, Serialize)]
pub struct CapturePreview {
    pub mime: &'static str,
    pub width: u32,
    pub height: u32,
    pub base64: String,
}

pub fn preview_bmp(frame: &Frame, max_width: u32, max_height: u32) -> Result<CapturePreview> {
    if max_width == 0 || max_height == 0 || max_width > 640 || max_height > 360 {
        return Err(Error::Limit("preview dimensions"));
    }
    let source_width = frame.display.width;
    let source_height = frame.display.height;
    let width_scale = max_width as f64 / source_width as f64;
    let height_scale = max_height as f64 / source_height as f64;
    let scale = width_scale.min(height_scale).min(1.0);
    let width = ((source_width as f64 * scale).floor() as u32).max(1);
    let height = ((source_height as f64 * scale).floor() as u32).max(1);
    let row_bytes = (width as usize)
        .checked_mul(3)
        .ok_or(Error::Limit("preview row bytes"))?;
    let row_stride = row_bytes
        .checked_add(3)
        .map(|value| value & !3)
        .ok_or(Error::Limit("preview row stride"))?;
    let pixel_bytes = row_stride
        .checked_mul(height as usize)
        .ok_or(Error::Limit("preview bytes"))?;
    let file_size = 54usize
        .checked_add(pixel_bytes)
        .ok_or(Error::Limit("preview bytes"))?;
    if file_size > 192 * 1024 {
        return Err(Error::Limit("preview encoded bytes"));
    }

    let mut bmp = vec![0u8; file_size];
    bmp[0..2].copy_from_slice(b"BM");
    bmp[2..6].copy_from_slice(&(file_size as u32).to_le_bytes());
    bmp[10..14].copy_from_slice(&54u32.to_le_bytes());
    bmp[14..18].copy_from_slice(&40u32.to_le_bytes());
    bmp[18..22].copy_from_slice(&(width as i32).to_le_bytes());
    bmp[22..26].copy_from_slice(&(height as i32).to_le_bytes());
    bmp[26..28].copy_from_slice(&1u16.to_le_bytes());
    bmp[28..30].copy_from_slice(&24u16.to_le_bytes());
    bmp[34..38].copy_from_slice(&(pixel_bytes as u32).to_le_bytes());

    for output_row in 0..height as usize {
        let display_y = height as usize - 1 - output_row;
        let source_y = display_y * source_height as usize / height as usize;
        let row_start = 54 + output_row * row_stride;
        for output_x in 0..width as usize {
            let source_x = output_x * source_width as usize / width as usize;
            let source = (source_y * source_width as usize + source_x) * 4;
            let target = row_start + output_x * 3;
            bmp[target] = frame.rgba[source + 2];
            bmp[target + 1] = frame.rgba[source + 1];
            bmp[target + 2] = frame.rgba[source];
        }
    }

    Ok(CapturePreview {
        mime: "image/bmp",
        width,
        height,
        base64: encode_base64(&bmp),
    })
}

fn encode_base64(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(bytes.len().div_ceil(3) * 4);
    let mut index = 0;
    while index + 3 <= bytes.len() {
        let value = ((bytes[index] as u32) << 16)
            | ((bytes[index + 1] as u32) << 8)
            | bytes[index + 2] as u32;
        output.push(TABLE[((value >> 18) & 63) as usize] as char);
        output.push(TABLE[((value >> 12) & 63) as usize] as char);
        output.push(TABLE[((value >> 6) & 63) as usize] as char);
        output.push(TABLE[(value & 63) as usize] as char);
        index += 3;
    }
    let remaining = bytes.len() - index;
    if remaining == 1 {
        let value = (bytes[index] as u32) << 16;
        output.push(TABLE[((value >> 18) & 63) as usize] as char);
        output.push(TABLE[((value >> 12) & 63) as usize] as char);
        output.push('=');
        output.push('=');
    } else if remaining == 2 {
        let value = ((bytes[index] as u32) << 16) | ((bytes[index + 1] as u32) << 8);
        output.push(TABLE[((value >> 18) & 63) as usize] as char);
        output.push(TABLE[((value >> 12) & 63) as usize] as char);
        output.push(TABLE[((value >> 6) & 63) as usize] as char);
        output.push('=');
    }
    output
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
