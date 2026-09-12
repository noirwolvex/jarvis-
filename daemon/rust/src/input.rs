use crate::{
    Error, Result,
    capture::Frame,
    governance::EmergencyLatch,
    types::ForegroundBinding,
};
use std::time::{Duration, Instant};

pub trait InputController: Send + Sync {
    fn click(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
    fn type_text(
        &self,
        frame: &Frame,
        text: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
}

fn validate_frame(frame: &Frame, x: i32, y: i32, emergency: &EmergencyLatch) -> Result<()> {
    emergency.check()?;
    if !frame.fresh(Instant::now(), Duration::from_secs(2)) {
        return Err(Error::Stale);
    }
    if !frame.contains(x, y) {
        return Err(Error::Denied("coordinate outside authorized display"));
    }
    Ok(())
}

fn validate_binding(binding: &ForegroundBinding) -> Result<()> {
    if binding.process_id == 0
        || binding.title.trim().is_empty()
        || binding.title.len() > 512
        || binding.title.contains('\0')
    {
        return Err(Error::Denied("invalid foreground binding"));
    }
    Ok(())
}

pub struct SimulationInput;
impl InputController for SimulationInput {
    fn click(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_binding(foreground)?;
        validate_frame(frame, x, y, emergency)
    }

    fn type_text(
        &self,
        frame: &Frame,
        text: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_binding(foreground)?;
        validate_frame(frame, frame.display.x, frame.display.y, emergency)?;
        if text.len() > 4096 || text.contains('\0') {
            return Err(Error::Limit("keyboard text"));
        }
        Ok(())
    }
}

#[cfg(all(feature = "native", target_os = "windows"))]
mod windows_foreground {
    use super::*;

    #[link(name = "user32")]
    unsafe extern "system" {
        fn GetForegroundWindow() -> isize;
        fn GetWindowThreadProcessId(hwnd: isize, process_id: *mut u32) -> u32;
        fn GetWindowTextW(hwnd: isize, text: *mut u16, max_count: i32) -> i32;
    }

    pub fn current() -> Result<Option<ForegroundBinding>> {
        let hwnd = unsafe { GetForegroundWindow() };
        if hwnd == 0 {
            return Ok(None);
        }
        let mut process_id = 0u32;
        unsafe { GetWindowThreadProcessId(hwnd, &mut process_id) };
        if process_id == 0 {
            return Ok(None);
        }
        let mut buffer = [0u16; 513];
        let length = unsafe { GetWindowTextW(hwnd, buffer.as_mut_ptr(), buffer.len() as i32) };
        if length <= 0 {
            return Ok(None);
        }
        let title = String::from_utf16_lossy(&buffer[..length as usize]);
        let binding = ForegroundBinding { process_id, title };
        validate_binding(&binding)?;
        Ok(Some(binding))
    }
}

pub fn current_foreground_binding() -> Result<Option<ForegroundBinding>> {
    #[cfg(all(feature = "native", target_os = "windows"))]
    {
        return windows_foreground::current();
    }
    #[cfg(not(all(feature = "native", target_os = "windows")))]
    {
        Ok(None)
    }
}

fn verify_foreground(expected: &ForegroundBinding) -> Result<()> {
    validate_binding(expected)?;
    let actual = current_foreground_binding()?
        .ok_or(Error::Denied("foreground window unavailable"))?;
    if actual.process_id != expected.process_id || actual.title != expected.title {
        return Err(Error::Denied("foreground window changed"));
    }
    Ok(())
}

/// Native input is exposed only on Windows and only after the caller binds the action
/// to the exact foreground process/title observed immediately before execution.
#[cfg(feature = "native")]
pub struct NativeInput;

#[cfg(all(feature = "native", target_os = "windows"))]
impl InputController for NativeInput {
    fn click(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Button, Coordinate, Direction, Enigo, Mouse, Settings};
        validate_frame(frame, x, y, emergency)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        verify_foreground(foreground)?;
        let mut input = Enigo::new(&Settings::default())
            .map_err(|_| Error::Operation("input connection failed".into()))?;
        emergency.check()?;
        verify_foreground(foreground)?;
        input
            .move_mouse(x, y, Coordinate::Abs)
            .map_err(|_| Error::Operation("pointer movement failed".into()))?;
        emergency.check()?;
        verify_foreground(foreground)?;
        input
            .button(Button::Left, Direction::Click)
            .map_err(|_| Error::Operation("click failed".into()))
    }

    fn type_text(
        &self,
        frame: &Frame,
        text: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Enigo, Keyboard, Settings};
        validate_frame(frame, frame.display.x, frame.display.y, emergency)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        if text.len() > 4096 || text.contains('\0') {
            return Err(Error::Limit("keyboard text"));
        }
        verify_foreground(foreground)?;
        let mut input = Enigo::new(&Settings::default())
            .map_err(|_| Error::Operation("input connection failed".into()))?;
        emergency.check()?;
        verify_foreground(foreground)?;
        input
            .text(text)
            .map_err(|_| Error::Operation("keyboard input failed".into()))
    }
}

#[cfg(all(feature = "native", not(target_os = "windows")))]
impl InputController for NativeInput {
    fn click(
        &self,
        _frame: &Frame,
        _x: i32,
        _y: i32,
        _foreground: &ForegroundBinding,
        _emergency: &EmergencyLatch,
    ) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }

    fn type_text(
        &self,
        _frame: &Frame,
        _text: &str,
        _foreground: &ForegroundBinding,
        _emergency: &EmergencyLatch,
    ) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
}
