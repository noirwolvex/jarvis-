use crate::{
    Error, Result,
    capture::Frame,
    governance::EmergencyLatch,
    types::{ForegroundBinding, MouseButton},
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
    fn click_button(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        button: MouseButton,
        clicks: u8,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
    fn pointer_move(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
    fn drag(
        &self,
        frame: &Frame,
        start_x: i32,
        start_y: i32,
        end_x: i32,
        end_y: i32,
        duration_ms: u64,
        button: MouseButton,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
    fn scroll(
        &self,
        frame: &Frame,
        clicks: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
    fn press_key(
        &self,
        frame: &Frame,
        key: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
    fn hotkey(
        &self,
        frame: &Frame,
        keys: &[String],
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

fn validate_keyboard_frame(frame: &Frame, emergency: &EmergencyLatch) -> Result<()> {
    validate_frame(frame, frame.display.x, frame.display.y, emergency)
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

fn validate_key_name(key: &str) -> Result<()> {
    let value = key.trim();
    if value.is_empty() || value.len() > 32 || value.contains('\0') {
        return Err(Error::Limit("keyboard key"));
    }
    Ok(())
}

fn validate_hotkey(keys: &[String]) -> Result<()> {
    if keys.is_empty() || keys.len() > 8 {
        return Err(Error::Limit("hotkey keys"));
    }
    for key in keys {
        validate_key_name(key)?;
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
        self.click_button(frame, x, y, MouseButton::Left, 1, foreground, emergency)
    }

    fn click_button(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        _button: MouseButton,
        clicks: u8,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_binding(foreground)?;
        validate_frame(frame, x, y, emergency)?;
        if !(1..=3).contains(&clicks) {
            return Err(Error::Limit("mouse click count"));
        }
        Ok(())
    }

    fn pointer_move(
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

    fn drag(
        &self,
        frame: &Frame,
        start_x: i32,
        start_y: i32,
        end_x: i32,
        end_y: i32,
        duration_ms: u64,
        _button: MouseButton,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_binding(foreground)?;
        validate_frame(frame, start_x, start_y, emergency)?;
        validate_frame(frame, end_x, end_y, emergency)?;
        if duration_ms > 2_000 {
            return Err(Error::Limit("drag duration"));
        }
        Ok(())
    }

    fn scroll(
        &self,
        frame: &Frame,
        clicks: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_binding(foreground)?;
        validate_keyboard_frame(frame, emergency)?;
        if !(-1000..=1000).contains(&clicks) {
            return Err(Error::Limit("scroll steps"));
        }
        Ok(())
    }

    fn press_key(
        &self,
        frame: &Frame,
        key: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_binding(foreground)?;
        validate_keyboard_frame(frame, emergency)?;
        validate_key_name(key)
    }

    fn hotkey(
        &self,
        frame: &Frame,
        keys: &[String],
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_binding(foreground)?;
        validate_keyboard_frame(frame, emergency)?;
        validate_hotkey(keys)
    }

    fn type_text(
        &self,
        frame: &Frame,
        text: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_binding(foreground)?;
        validate_keyboard_frame(frame, emergency)?;
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
    let actual =
        current_foreground_binding()?.ok_or(Error::Denied("foreground window unavailable"))?;
    if actual.process_id != expected.process_id || actual.title != expected.title {
        return Err(Error::Denied("foreground window changed"));
    }
    Ok(())
}

#[cfg(all(feature = "native", target_os = "windows"))]
fn native_button(button: MouseButton) -> enigo::Button {
    match button {
        MouseButton::Left => enigo::Button::Left,
        MouseButton::Right => enigo::Button::Right,
        MouseButton::Middle => enigo::Button::Middle,
    }
}

#[cfg(all(feature = "native", target_os = "windows"))]
fn native_key(value: &str) -> Result<enigo::Key> {
    use enigo::Key;

    validate_key_name(value)?;
    let normalized = value.trim().to_ascii_lowercase();
    if normalized.chars().count() == 1 {
        return Ok(Key::Unicode(normalized.chars().next().unwrap()));
    }
    let key = match normalized.as_str() {
        "ctrl" | "control" => Key::Control,
        "alt" => Key::Alt,
        "shift" => Key::Shift,
        "win" | "windows" | "meta" | "super" => Key::Meta,
        "enter" | "return" => Key::Return,
        "esc" | "escape" => Key::Escape,
        "tab" => Key::Tab,
        "space" => Key::Space,
        "backspace" => Key::Backspace,
        "delete" | "del" => Key::Delete,
        "home" => Key::Home,
        "end" => Key::End,
        "pageup" | "page_up" => Key::PageUp,
        "pagedown" | "page_down" => Key::PageDown,
        "left" | "leftarrow" => Key::LeftArrow,
        "right" | "rightarrow" => Key::RightArrow,
        "up" | "uparrow" => Key::UpArrow,
        "down" | "downarrow" => Key::DownArrow,
        "f1" => Key::F1,
        "f2" => Key::F2,
        "f3" => Key::F3,
        "f4" => Key::F4,
        "f5" => Key::F5,
        "f6" => Key::F6,
        "f7" => Key::F7,
        "f8" => Key::F8,
        "f9" => Key::F9,
        "f10" => Key::F10,
        "f11" => Key::F11,
        "f12" => Key::F12,
        _ => return Err(Error::Unsupported("keyboard key is not supported by native input")),
    };
    Ok(key)
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
        self.click_button(frame, x, y, MouseButton::Left, 1, foreground, emergency)
    }

    fn click_button(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        button: MouseButton,
        clicks: u8,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Coordinate, Direction, Enigo, Mouse, Settings};

        validate_frame(frame, x, y, emergency)?;
        if frame.simulation {
            return Err(Error::Denied("simulation evidence cannot authorize native input"));
        }
        if !(1..=3).contains(&clicks) {
            return Err(Error::Limit("mouse click count"));
        }
        verify_foreground(foreground)?;
        let mut input = Enigo::new(&Settings::default())
            .map_err(|_| Error::Operation("input connection failed".into()))?;
        emergency.check()?;
        verify_foreground(foreground)?;
        input
            .move_mouse(x, y, Coordinate::Abs)
            .map_err(|_| Error::Operation("pointer movement failed".into()))?;
        for index in 0..clicks {
            emergency.check()?;
            verify_foreground(foreground)?;
            input
                .button(native_button(button), Direction::Click)
                .map_err(|_| Error::Operation("click failed".into()))?;
            if index + 1 < clicks {
                std::thread::sleep(Duration::from_millis(45));
            }
        }
        Ok(())
    }

    fn pointer_move(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Coordinate, Enigo, Mouse, Settings};

        validate_frame(frame, x, y, emergency)?;
        if frame.simulation {
            return Err(Error::Denied("simulation evidence cannot authorize native input"));
        }
        verify_foreground(foreground)?;
        let mut input = Enigo::new(&Settings::default())
            .map_err(|_| Error::Operation("input connection failed".into()))?;
        emergency.check()?;
        verify_foreground(foreground)?;
        input
            .move_mouse(x, y, Coordinate::Abs)
            .map_err(|_| Error::Operation("pointer movement failed".into()))
    }

    fn drag(
        &self,
        frame: &Frame,
        start_x: i32,
        start_y: i32,
        end_x: i32,
        end_y: i32,
        duration_ms: u64,
        button: MouseButton,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Coordinate, Direction, Enigo, Mouse, Settings};

        validate_frame(frame, start_x, start_y, emergency)?;
        validate_frame(frame, end_x, end_y, emergency)?;
        if frame.simulation {
            return Err(Error::Denied("simulation evidence cannot authorize native input"));
        }
        if duration_ms > 2_000 {
            return Err(Error::Limit("drag duration"));
        }
        verify_foreground(foreground)?;
        let mut input = Enigo::new(&Settings::default())
            .map_err(|_| Error::Operation("input connection failed".into()))?;
        input
            .move_mouse(start_x, start_y, Coordinate::Abs)
            .map_err(|_| Error::Operation("drag start movement failed".into()))?;
        emergency.check()?;
        verify_foreground(foreground)?;
        input
            .button(native_button(button), Direction::Press)
            .map_err(|_| Error::Operation("mouse button press failed".into()))?;

        let steps = ((duration_ms.max(16) + 15) / 16).clamp(1, 125);
        let mut movement_result = Ok(());
        for step in 1..=steps {
            if let Err(error) = emergency.check().and_then(|_| verify_foreground(foreground)) {
                movement_result = Err(error);
                break;
            }
            let progress = step as f64 / steps as f64;
            let x = start_x as f64 + (end_x - start_x) as f64 * progress;
            let y = start_y as f64 + (end_y - start_y) as f64 * progress;
            if input.move_mouse(x.round() as i32, y.round() as i32, Coordinate::Abs).is_err() {
                movement_result = Err(Error::Operation("drag movement failed".into()));
                break;
            }
            if step < steps && duration_ms > 0 {
                std::thread::sleep(Duration::from_millis((duration_ms / steps).max(1)));
            }
        }

        let release_result = input
            .button(native_button(button), Direction::Release)
            .map_err(|_| Error::Operation("mouse button release failed".into()));
        movement_result?;
        release_result
    }

    fn scroll(
        &self,
        frame: &Frame,
        clicks: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Axis, Enigo, Mouse, Settings};

        validate_keyboard_frame(frame, emergency)?;
        if frame.simulation {
            return Err(Error::Denied("simulation evidence cannot authorize native input"));
        }
        if !(-1000..=1000).contains(&clicks) {
            return Err(Error::Limit("scroll steps"));
        }
        verify_foreground(foreground)?;
        let mut input = Enigo::new(&Settings::default())
            .map_err(|_| Error::Operation("input connection failed".into()))?;
        let mut remaining = clicks;
        while remaining != 0 {
            emergency.check()?;
            verify_foreground(foreground)?;
            let magnitude = remaining.abs().min(8);
            let chunk = if remaining > 0 { magnitude } else { -magnitude };
            // Public JARVIS semantics follow PyAutoGUI: positive means scroll up.
            // Enigo's vertical axis uses positive for down, so invert the sign.
            input
                .scroll(-chunk, Axis::Vertical)
                .map_err(|_| Error::Operation("scroll failed".into()))?;
            remaining -= chunk;
        }
        Ok(())
    }

    fn press_key(
        &self,
        frame: &Frame,
        key: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Direction, Enigo, Keyboard, Settings};

        validate_keyboard_frame(frame, emergency)?;
        if frame.simulation {
            return Err(Error::Denied("simulation evidence cannot authorize native input"));
        }
        let key = native_key(key)?;
        verify_foreground(foreground)?;
        let mut input = Enigo::new(&Settings::default())
            .map_err(|_| Error::Operation("input connection failed".into()))?;
        emergency.check()?;
        verify_foreground(foreground)?;
        input
            .key(key, Direction::Click)
            .map_err(|_| Error::Operation("keyboard key press failed".into()))
    }

    fn hotkey(
        &self,
        frame: &Frame,
        keys: &[String],
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Direction, Enigo, Keyboard, Settings};

        validate_keyboard_frame(frame, emergency)?;
        validate_hotkey(keys)?;
        if frame.simulation {
            return Err(Error::Denied("simulation evidence cannot authorize native input"));
        }
        let mapped: Vec<_> = keys
            .iter()
            .map(|value| native_key(value))
            .collect::<Result<Vec<_>>>()?;
        verify_foreground(foreground)?;
        let mut input = Enigo::new(&Settings::default())
            .map_err(|_| Error::Operation("input connection failed".into()))?;
        let mut pressed = Vec::new();
        for key in &mapped {
            if let Err(error) = emergency.check().and_then(|_| verify_foreground(foreground)) {
                for prior in pressed.iter().rev() {
                    let _ = input.key(*prior, Direction::Release);
                }
                return Err(error);
            }
            if input.key(*key, Direction::Press).is_err() {
                for prior in pressed.iter().rev() {
                    let _ = input.key(*prior, Direction::Release);
                }
                return Err(Error::Operation("hotkey press failed".into()));
            }
            pressed.push(*key);
        }
        for key in pressed.iter().rev() {
            input
                .key(*key, Direction::Release)
                .map_err(|_| Error::Operation("hotkey release failed".into()))?;
        }
        Ok(())
    }

    fn type_text(
        &self,
        frame: &Frame,
        text: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        use enigo::{Enigo, Keyboard, Settings};

        validate_keyboard_frame(frame, emergency)?;
        if frame.simulation {
            return Err(Error::Denied("simulation evidence cannot authorize native input"));
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
    fn click(&self, _frame: &Frame, _x: i32, _y: i32, _foreground: &ForegroundBinding, _emergency: &EmergencyLatch) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn click_button(&self, _frame: &Frame, _x: i32, _y: i32, _button: MouseButton, _clicks: u8, _foreground: &ForegroundBinding, _emergency: &EmergencyLatch) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn pointer_move(&self, _frame: &Frame, _x: i32, _y: i32, _foreground: &ForegroundBinding, _emergency: &EmergencyLatch) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn drag(&self, _frame: &Frame, _start_x: i32, _start_y: i32, _end_x: i32, _end_y: i32, _duration_ms: u64, _button: MouseButton, _foreground: &ForegroundBinding, _emergency: &EmergencyLatch) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn scroll(&self, _frame: &Frame, _clicks: i32, _foreground: &ForegroundBinding, _emergency: &EmergencyLatch) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn press_key(&self, _frame: &Frame, _key: &str, _foreground: &ForegroundBinding, _emergency: &EmergencyLatch) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn hotkey(&self, _frame: &Frame, _keys: &[String], _foreground: &ForegroundBinding, _emergency: &EmergencyLatch) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn type_text(&self, _frame: &Frame, _text: &str, _foreground: &ForegroundBinding, _emergency: &EmergencyLatch) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
}
