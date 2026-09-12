use crate::{Error, Result, capture::Frame, governance::EmergencyLatch};
use std::time::{Duration, Instant};

pub trait InputController: Send + Sync {
    fn click(&self, frame: &Frame, x: i32, y: i32, emergency: &EmergencyLatch) -> Result<()>;
}
fn validate(frame: &Frame, x: i32, y: i32, emergency: &EmergencyLatch) -> Result<()> {
    emergency.check()?;
    if !frame.fresh(Instant::now(), Duration::from_secs(2)) { return Err(Error::Stale); }
    if !frame.contains(x, y) { return Err(Error::Denied("coordinate outside authorized display")); }
    Ok(())
}
pub struct SimulationInput;
impl InputController for SimulationInput {
    fn click(&self, frame: &Frame, x: i32, y: i32, emergency: &EmergencyLatch) -> Result<()> { validate(frame, x, y, emergency) }
}

/// Experimental primitive, deliberately not exposed by the daemon until a foreground
/// window/element binding adapter can close the cross-application target race.
#[cfg(feature = "native")]
pub struct NativeInput;
#[cfg(feature = "native")]
impl InputController for NativeInput {
    fn click(&self, frame: &Frame, x: i32, y: i32, emergency: &EmergencyLatch) -> Result<()> {
        use enigo::{Button, Coordinate, Direction, Enigo, Mouse, Settings};
        validate(frame, x, y, emergency)?;
        if frame.simulation { return Err(Error::Denied("simulation evidence cannot authorize native input")); }
        let mut input = Enigo::new(&Settings::default()).map_err(|_| Error::Operation("input connection failed".into()))?;
        emergency.check()?;
        input.move_mouse(x, y, Coordinate::Abs).map_err(|_| Error::Operation("pointer movement failed".into()))?;
        emergency.check()?;
        input.button(Button::Left, Direction::Click).map_err(|_| Error::Operation("click failed".into()))
    }
}
