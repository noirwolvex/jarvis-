use crate::{
    Error, Result,
    capture::Frame,
    governance::EmergencyLatch,
    types::{ForegroundBinding, MouseButton},
};
use std::time::{Duration, Instant};

#[cfg(any(all(feature = "native", target_os = "windows"), test))]
const UNICODE_CHUNK_UNITS: usize = 256;
#[cfg(any(all(feature = "native", target_os = "windows"), test))]
const SCROLL_CHUNK_STEPS: i32 = 32;

#[cfg(any(all(feature = "native", target_os = "windows"), test))]
fn scroll_chunk(remaining: i32) -> i32 {
    remaining.clamp(-SCROLL_CHUNK_STEPS, SCROLL_CHUNK_STEPS)
}

pub trait InputController: Send + Sync {
    fn click(
        &self,
        frame: &Frame,
        x: i32,
        y: i32,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
    #[expect(
        clippy::too_many_arguments,
        reason = "Keep the existing explicit input-adapter contract compatible"
    )]
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
        duration_ms: u64,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()>;
    #[expect(
        clippy::too_many_arguments,
        reason = "Endpoints and authorization are explicit in the existing adapter contract"
    )]
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
    if binding.hwnd == 0
        || binding.process_id == 0
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

fn validate_text(text: &str) -> Result<()> {
    if text.is_empty() || text.chars().count() > 4096 || text.contains('\0') {
        return Err(Error::Limit("keyboard text"));
    }
    Ok(())
}

#[cfg(any(all(feature = "native", target_os = "windows"), test))]
fn unicode_chunks(text: &str, mut deliver: impl FnMut(&[u16]) -> Result<()>) -> Result<()> {
    let mut chunk = [0u16; UNICODE_CHUNK_UNITS];
    let mut used = 0;
    for character in text.chars() {
        if used + character.len_utf16() > chunk.len() {
            deliver(&chunk[..used])?;
            used = 0;
        }
        let mut encoded = [0u16; 2];
        let units = character.encode_utf16(&mut encoded);
        chunk[used..used + units.len()].copy_from_slice(units);
        used += units.len();
    }
    if used > 0 {
        deliver(&chunk[..used])?;
    }
    Ok(())
}

#[cfg(test)]
mod unicode_tests {
    use super::*;

    #[test]
    fn arabic_and_emoji_use_character_limit_instead_of_utf8_byte_limit() {
        assert!(validate_text(&"ع".repeat(4096)).is_ok());
        assert!(validate_text(&"🦀".repeat(4096)).is_ok());
        assert!(validate_text(&"🦀".repeat(4097)).is_err());
        assert!(validate_text("").is_err());
        assert!(validate_text("hello\0world").is_err());
    }

    #[test]
    fn chunk_boundaries_never_split_surrogate_pairs() {
        let text = format!("{}🦀{}ع", "x".repeat(63), "🎹".repeat(70));
        let mut recovered = String::new();
        unicode_chunks(&text, |chunk| {
            assert!(chunk.len() <= UNICODE_CHUNK_UNITS);
            recovered.push_str(
                &String::from_utf16(chunk).expect("each chunk contains complete scalars"),
            );
            Ok(())
        })
        .unwrap();
        assert_eq!(recovered, text);
    }

    #[test]
    fn long_unicode_input_uses_high_throughput_bounded_chunks() {
        let mut calls = 0;
        unicode_chunks(&"ع".repeat(4096), |chunk| {
            calls += 1;
            assert!(chunk.len() <= UNICODE_CHUNK_UNITS);
            Ok(())
        })
        .unwrap();
        assert_eq!(calls, 4096 / UNICODE_CHUNK_UNITS);
    }

    #[test]
    fn scroll_chunks_preserve_direction_and_reduce_dispatch_count() {
        assert_eq!(scroll_chunk(1000), SCROLL_CHUNK_STEPS);
        assert_eq!(scroll_chunk(-1000), -SCROLL_CHUNK_STEPS);
        assert_eq!(scroll_chunk(7), 7);
        assert_eq!(scroll_chunk(-7), -7);
        assert_eq!(scroll_chunk(0), 0);
        assert!((1000 + SCROLL_CHUNK_STEPS - 1) / SCROLL_CHUNK_STEPS <= 32);
    }

    #[test]
    fn cancelled_chunk_stops_without_retry_or_delivering_remainder() {
        let mut calls = 0;
        let result = unicode_chunks(&"🎹".repeat(100), |_| {
            calls += 1;
            Err(Error::Emergency)
        });
        assert!(matches!(result, Err(Error::Emergency)));
        assert_eq!(calls, 1);
    }
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
        duration_ms: u64,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        if duration_ms > 2_000 {
            return Err(Error::Limit("pointer motion duration"));
        }
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
        validate_text(text)
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
        let title = if length > 0 {
            String::from_utf16_lossy(&buffer[..length as usize])
        } else {
            String::new()
        };
        let binding = ForegroundBinding {
            hwnd: hwnd as u64,
            process_id,
            title,
        };
        validate_binding(&binding)?;
        Ok(Some(binding))
    }
}

pub fn current_foreground_binding() -> Result<Option<ForegroundBinding>> {
    #[cfg(all(feature = "native", target_os = "windows"))]
    {
        windows_foreground::current()
    }
    #[cfg(not(all(feature = "native", target_os = "windows")))]
    {
        Ok(None)
    }
}

/// Read physical desktop coordinates without generating input.
pub fn current_pointer_position() -> Result<Option<(i32, i32)>> {
    #[cfg(all(feature = "native", target_os = "windows"))]
    {
        windows_input::position().map(Some)
    }
    #[cfg(not(all(feature = "native", target_os = "windows")))]
    {
        Ok(None)
    }
}

#[cfg(all(feature = "native", target_os = "windows"))]
fn verify_foreground(expected: &ForegroundBinding) -> Result<()> {
    validate_binding(expected)?;
    let actual =
        current_foreground_binding()?.ok_or(Error::Denied("foreground window unavailable"))?;
    if actual.hwnd != expected.hwnd || actual.process_id != expected.process_id {
        return Err(Error::Denied("foreground window changed"));
    }
    Ok(())
}

#[cfg(all(feature = "native", target_os = "windows"))]
mod windows_input {
    use super::*;
    use std::mem::size_of;

    const INPUT_MOUSE: u32 = 0;
    const INPUT_KEYBOARD: u32 = 1;

    const MOUSEEVENTF_LEFTDOWN: u32 = 0x0002;
    const MOUSEEVENTF_LEFTUP: u32 = 0x0004;
    const MOUSEEVENTF_RIGHTDOWN: u32 = 0x0008;
    const MOUSEEVENTF_RIGHTUP: u32 = 0x0010;
    const MOUSEEVENTF_MIDDLEDOWN: u32 = 0x0020;
    const MOUSEEVENTF_MIDDLEUP: u32 = 0x0040;
    const MOUSEEVENTF_WHEEL: u32 = 0x0800;
    const WHEEL_DELTA: i32 = 120;

    const KEYEVENTF_EXTENDEDKEY: u32 = 0x0001;
    const KEYEVENTF_KEYUP: u32 = 0x0002;
    const KEYEVENTF_UNICODE: u32 = 0x0004;

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct Point {
        x: i32,
        y: i32,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct MouseInput {
        dx: i32,
        dy: i32,
        mouse_data: u32,
        flags: u32,
        time: u32,
        extra_info: usize,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct KeyboardInput {
        vk: u16,
        scan: u16,
        flags: u32,
        time: u32,
        extra_info: usize,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    union InputData {
        mouse: MouseInput,
        keyboard: KeyboardInput,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct Input {
        kind: u32,
        data: InputData,
    }

    #[link(name = "user32")]
    unsafe extern "system" {
        fn SetPhysicalCursorPos(x: i32, y: i32) -> i32;
        fn GetPhysicalCursorPos(point: *mut Point) -> i32;
        fn SendInput(count: u32, inputs: *const Input, size: i32) -> u32;
    }

    fn mouse_input(flags: u32, mouse_data: u32) -> Input {
        Input {
            kind: INPUT_MOUSE,
            data: InputData {
                mouse: MouseInput {
                    dx: 0,
                    dy: 0,
                    mouse_data,
                    flags,
                    time: 0,
                    extra_info: 0,
                },
            },
        }
    }

    fn keyboard_input(vk: u16, scan: u16, flags: u32) -> Input {
        Input {
            kind: INPUT_KEYBOARD,
            data: InputData {
                keyboard: KeyboardInput {
                    vk,
                    scan,
                    flags,
                    time: 0,
                    extra_info: 0,
                },
            },
        }
    }

    fn send(inputs: &[Input], operation: &'static str) -> Result<()> {
        if inputs.is_empty() {
            return Ok(());
        }
        let inserted = unsafe {
            SendInput(
                inputs.len() as u32,
                inputs.as_ptr(),
                size_of::<Input>() as i32,
            )
        };
        if inserted != inputs.len() as u32 {
            return Err(Error::Operation(format!(
                "{operation} inserted {inserted}/{} events; Windows may be blocking synthetic input at an integrity/UIPI boundary",
                inputs.len()
            )));
        }
        Ok(())
    }

    pub fn move_to(x: i32, y: i32) -> Result<()> {
        if unsafe { SetPhysicalCursorPos(x, y) } == 0 {
            return Err(Error::Operation("physical pointer movement failed".into()));
        }
        let point = position()?;
        if point != (x, y) {
            return Err(Error::Operation(format!(
                "physical pointer reached ({}, {}) instead of ({}, {})",
                point.0, point.1, x, y
            )));
        }
        Ok(())
    }

    pub fn position() -> Result<(i32, i32)> {
        let mut point = Point { x: 0, y: 0 };
        if unsafe { GetPhysicalCursorPos(&mut point) } == 0 {
            return Err(Error::Operation(
                "physical pointer verification failed".into(),
            ));
        }
        Ok((point.x, point.y))
    }

    fn button_flags(button: MouseButton) -> (u32, u32) {
        match button {
            MouseButton::Left => (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            MouseButton::Right => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            MouseButton::Middle => (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }
    }

    pub fn click(button: MouseButton) -> Result<()> {
        let (down, up) = button_flags(button);
        match send(&[mouse_input(down, 0), mouse_input(up, 0)], "mouse click") {
            Ok(()) => Ok(()),
            Err(error) => {
                // A short SendInput write may have delivered only the button-down event.
                // Always issue a best-effort release so a failed click cannot leave the
                // user's physical pointer logically held down.
                let _ = send(&[mouse_input(up, 0)], "mouse click cleanup");
                Err(error)
            }
        }
    }

    pub fn button_down(button: MouseButton) -> Result<()> {
        let (down, _) = button_flags(button);
        send(&[mouse_input(down, 0)], "mouse button press")
    }

    pub fn button_up(button: MouseButton) -> Result<()> {
        let (_, up) = button_flags(button);
        send(&[mouse_input(up, 0)], "mouse button release")
    }

    pub fn scroll(clicks: i32) -> Result<()> {
        if clicks == 0 {
            return Ok(());
        }
        if clicks.abs() > SCROLL_CHUNK_STEPS {
            return Err(Error::Limit("native scroll batch"));
        }
        // Preserve one physical wheel notch per INPUT record so applications that
        // clamp or special-case large wheel deltas behave the same as real repeated
        // wheel input, while one SendInput call still carries the whole bounded batch.
        let data = if clicks > 0 {
            WHEEL_DELTA as u32
        } else {
            (-WHEEL_DELTA) as u32
        };
        let inputs = vec![mouse_input(MOUSEEVENTF_WHEEL, data); clicks.abs() as usize];
        send(&inputs, "mouse wheel")
    }

    fn virtual_key(value: &str) -> Result<(u16, u32)> {
        validate_key_name(value)?;
        let normalized = value.trim().to_ascii_lowercase();
        if normalized.len() == 1 {
            let byte = normalized.as_bytes()[0];
            if byte.is_ascii_alphabetic() {
                return Ok((byte.to_ascii_uppercase() as u16, 0));
            }
            if byte.is_ascii_digit() {
                return Ok((byte as u16, 0));
            }
        }
        let (key, flags) = match normalized.as_str() {
            "ctrl" | "control" => (0x11, 0),
            "alt" => (0x12, 0),
            "shift" => (0x10, 0),
            "win" | "windows" | "meta" | "super" => (0x5B, 0),
            "enter" | "return" => (0x0D, 0),
            "esc" | "escape" => (0x1B, 0),
            "tab" => (0x09, 0),
            "space" => (0x20, 0),
            "backspace" => (0x08, 0),
            "delete" | "del" => (0x2E, KEYEVENTF_EXTENDEDKEY),
            "home" => (0x24, KEYEVENTF_EXTENDEDKEY),
            "end" => (0x23, KEYEVENTF_EXTENDEDKEY),
            "pageup" | "page_up" => (0x21, KEYEVENTF_EXTENDEDKEY),
            "pagedown" | "page_down" => (0x22, KEYEVENTF_EXTENDEDKEY),
            "left" | "leftarrow" => (0x25, KEYEVENTF_EXTENDEDKEY),
            "up" | "uparrow" => (0x26, KEYEVENTF_EXTENDEDKEY),
            "right" | "rightarrow" => (0x27, KEYEVENTF_EXTENDEDKEY),
            "down" | "downarrow" => (0x28, KEYEVENTF_EXTENDEDKEY),
            "f1" => (0x70, 0),
            "f2" => (0x71, 0),
            "f3" => (0x72, 0),
            "f4" => (0x73, 0),
            "f5" => (0x74, 0),
            "f6" => (0x75, 0),
            "f7" => (0x76, 0),
            "f8" => (0x77, 0),
            "f9" => (0x78, 0),
            "f10" => (0x79, 0),
            "f11" => (0x7A, 0),
            "f12" => (0x7B, 0),
            _ => {
                return Err(Error::Unsupported(
                    "keyboard key is not supported by Windows native input",
                ));
            }
        };
        Ok((key, flags))
    }

    pub fn press_key(value: &str) -> Result<()> {
        let (key, flags) = virtual_key(value)?;
        match send(
            &[
                keyboard_input(key, 0, flags),
                keyboard_input(key, 0, flags | KEYEVENTF_KEYUP),
            ],
            "keyboard key press",
        ) {
            Ok(()) => Ok(()),
            Err(error) => {
                let _ = send(
                    &[keyboard_input(key, 0, flags | KEYEVENTF_KEYUP)],
                    "keyboard key cleanup",
                );
                Err(error)
            }
        }
    }

    pub fn hotkey(values: &[String]) -> Result<()> {
        let keys = values
            .iter()
            .map(|value| virtual_key(value))
            .collect::<Result<Vec<_>>>()?;
        let mut events = Vec::with_capacity(keys.len() * 2);
        for (key, flags) in &keys {
            events.push(keyboard_input(*key, 0, *flags));
        }
        for (key, flags) in keys.iter().rev() {
            events.push(keyboard_input(*key, 0, *flags | KEYEVENTF_KEYUP));
        }
        match send(&events, "keyboard hotkey") {
            Ok(()) => Ok(()),
            Err(error) => {
                let releases = keys
                    .iter()
                    .rev()
                    .map(|(key, flags)| keyboard_input(*key, 0, *flags | KEYEVENTF_KEYUP))
                    .collect::<Vec<_>>();
                let _ = send(&releases, "keyboard hotkey cleanup");
                Err(error)
            }
        }
    }

    pub fn type_text(
        text: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        let mut events = Vec::with_capacity(128);
        unicode_chunks(text, |chunk| {
            emergency.check()?;
            verify_foreground(foreground)?;
            events.clear();
            for unit in chunk {
                events.push(keyboard_input(0, *unit, KEYEVENTF_UNICODE));
                events.push(keyboard_input(
                    0,
                    *unit,
                    KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                ));
            }
            if let Err(error) = send(&events, "unicode keyboard input") {
                // Unicode key-down events are not modifiers, but release every unit
                // defensively if Windows accepted only part of this chunk.
                let releases = chunk
                    .iter()
                    .map(|unit| keyboard_input(0, *unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
                    .collect::<Vec<_>>();
                let _ = send(&releases, "unicode keyboard cleanup");
                return Err(error);
            }
            Ok(())
        })
    }
}

/// Native input is exposed only on Windows and only after the caller binds the action
/// to the exact foreground HWND/process observed immediately before execution. Window
/// titles are diagnostic only because legitimate applications change titles frequently.
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
        validate_frame(frame, x, y, emergency)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        if !(1..=3).contains(&clicks) {
            return Err(Error::Limit("mouse click count"));
        }
        verify_foreground(foreground)?;
        emergency.check()?;
        verify_foreground(foreground)?;
        windows_input::move_to(x, y)?;
        for index in 0..clicks {
            emergency.check()?;
            verify_foreground(foreground)?;
            windows_input::click(button)?;
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
        duration_ms: u64,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_frame(frame, x, y, emergency)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        verify_foreground(foreground)?;
        let from = windows_input::position()?;
        let motion = crate::motion::Motion::new(from, (x, y), duration_ms)?;
        if duration_ms > 0 && !frame.contains(from.0, from.1) {
            return Err(Error::Denied(
                "smooth movement starts outside authorized display; use duration_ms=0 for cross-display repositioning",
            ));
        }
        let started = Instant::now();
        motion.run(
            || started.elapsed(),
            std::thread::sleep,
            || {
                emergency
                    .check()
                    .and_then(|_| verify_foreground(foreground))
            },
            windows_input::move_to,
        )
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
        validate_frame(frame, start_x, start_y, emergency)?;
        validate_frame(frame, end_x, end_y, emergency)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        if duration_ms > 2_000 {
            return Err(Error::Limit("drag duration"));
        }
        verify_foreground(foreground)?;
        windows_input::move_to(start_x, start_y)?;
        emergency.check()?;
        verify_foreground(foreground)?;
        let motion = crate::motion::Motion::new((start_x, start_y), (end_x, end_y), duration_ms)?;
        if let Err(error) = windows_input::button_down(button) {
            let _ = windows_input::button_up(button);
            return Err(error);
        }
        let started = Instant::now();
        let movement_result = motion.run(
            || started.elapsed(),
            std::thread::sleep,
            || {
                emergency
                    .check()
                    .and_then(|_| verify_foreground(foreground))
            },
            windows_input::move_to,
        );

        let release_result = windows_input::button_up(button);
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
        validate_keyboard_frame(frame, emergency)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        if !(-1000..=1000).contains(&clicks) {
            return Err(Error::Limit("scroll steps"));
        }
        verify_foreground(foreground)?;
        let mut remaining = clicks;
        while remaining != 0 {
            emergency.check()?;
            verify_foreground(foreground)?;
            let chunk = scroll_chunk(remaining);
            // Keep emergency/foreground checks between bounded batches while reducing
            // Win32 SendInput overhead for long scrolls. Positive means scroll up.
            windows_input::scroll(chunk)?;
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
        validate_keyboard_frame(frame, emergency)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        validate_key_name(key)?;
        verify_foreground(foreground)?;
        emergency.check()?;
        verify_foreground(foreground)?;
        windows_input::press_key(key)
    }

    fn hotkey(
        &self,
        frame: &Frame,
        keys: &[String],
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_keyboard_frame(frame, emergency)?;
        validate_hotkey(keys)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        verify_foreground(foreground)?;
        emergency.check()?;
        verify_foreground(foreground)?;
        windows_input::hotkey(keys)
    }

    fn type_text(
        &self,
        frame: &Frame,
        text: &str,
        foreground: &ForegroundBinding,
        emergency: &EmergencyLatch,
    ) -> Result<()> {
        validate_keyboard_frame(frame, emergency)?;
        if frame.simulation {
            return Err(Error::Denied(
                "simulation evidence cannot authorize native input",
            ));
        }
        validate_text(text)?;
        verify_foreground(foreground)?;
        emergency.check()?;
        verify_foreground(foreground)?;
        windows_input::type_text(text, foreground, emergency)
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
    fn click_button(
        &self,
        _frame: &Frame,
        _x: i32,
        _y: i32,
        _button: MouseButton,
        _clicks: u8,
        _foreground: &ForegroundBinding,
        _emergency: &EmergencyLatch,
    ) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn pointer_move(
        &self,
        _frame: &Frame,
        _x: i32,
        _y: i32,
        _duration_ms: u64,
        _foreground: &ForegroundBinding,
        _emergency: &EmergencyLatch,
    ) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn drag(
        &self,
        _frame: &Frame,
        _start_x: i32,
        _start_y: i32,
        _end_x: i32,
        _end_y: i32,
        _duration_ms: u64,
        _button: MouseButton,
        _foreground: &ForegroundBinding,
        _emergency: &EmergencyLatch,
    ) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn scroll(
        &self,
        _frame: &Frame,
        _clicks: i32,
        _foreground: &ForegroundBinding,
        _emergency: &EmergencyLatch,
    ) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn press_key(
        &self,
        _frame: &Frame,
        _key: &str,
        _foreground: &ForegroundBinding,
        _emergency: &EmergencyLatch,
    ) -> Result<()> {
        Err(Error::Unsupported("native input is Windows-only"))
    }
    fn hotkey(
        &self,
        _frame: &Frame,
        _keys: &[String],
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
