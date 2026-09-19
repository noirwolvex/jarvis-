//! Bounded pointer trajectories driven by elapsed time, independent of OS scheduling.
use crate::{Error, Result};
use std::time::Duration;

pub(crate) const MAX_DURATION_MS: u64 = 2_000;
const TICK: Duration = Duration::from_millis(8);

pub(crate) struct Motion {
    from: (i32, i32),
    to: (i32, i32),
    duration: Duration,
}

impl Motion {
    pub(crate) fn new(from: (i32, i32), to: (i32, i32), duration_ms: u64) -> Result<Self> {
        if duration_ms > MAX_DURATION_MS {
            return Err(Error::Limit("pointer motion duration"));
        }
        Ok(Self {
            from,
            to,
            duration: Duration::from_millis(duration_ms),
        })
    }

    fn position(&self, elapsed: Duration) -> (i32, i32) {
        if elapsed >= self.duration || self.from == self.to {
            return self.to;
        }
        let t = elapsed.as_secs_f64() / self.duration.as_secs_f64();
        let eased = t * t * (3.0 - 2.0 * t);
        let axis = |start: i32, end: i32| {
            (f64::from(start) + (f64::from(end) - f64::from(start)) * eased).round() as i32
        };
        (axis(self.from.0, self.to.0), axis(self.from.1, self.to.1))
    }

    /// Check authority before every delivered point; never catch up missed ticks with a burst.
    pub(crate) fn run(
        &self,
        mut elapsed: impl FnMut() -> Duration,
        mut wait: impl FnMut(Duration),
        mut guard: impl FnMut() -> Result<()>,
        mut deliver: impl FnMut(i32, i32) -> Result<()>,
    ) -> Result<()> {
        let mut previous = self.from;
        loop {
            guard()?;
            let now = elapsed();
            let done = now >= self.duration || self.from == self.to;
            let point = self.position(now);
            if done || point != previous {
                deliver(point.0, point.1)?;
                previous = point;
            }
            if done {
                return Ok(());
            }
            // Include OS input/verification time in the budget instead of adding a full
            // tick to it. A delayed scheduler jumps to the current trajectory position.
            let remaining = self.duration.saturating_sub(elapsed());
            if !remaining.is_zero() {
                wait(remaining.min(TICK));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    #[test]
    fn duration_tracks_elapsed_time_and_lands_on_exact_negative_coordinate() {
        let now = Cell::new(Duration::ZERO);
        let points = RefCell::new(Vec::new());
        Motion::new((500, 500), (-300, -50), 80)
            .unwrap()
            .run(
                || now.get(),
                |delay| now.set(now.get() + delay),
                || Ok(()),
                |x, y| {
                    points.borrow_mut().push((x, y));
                    now.set(now.get() + Duration::from_millis(3));
                    Ok(())
                },
            )
            .unwrap();
        assert_eq!(points.borrow().last(), Some(&(-300, -50)));
        assert!(now.get() <= Duration::from_millis(83));
        assert!(
            points
                .borrow()
                .windows(2)
                .all(|pair| pair[1].0 <= pair[0].0 && pair[1].1 <= pair[0].1)
        );
    }

    #[test]
    fn cancellation_during_wait_prevents_the_next_input() {
        let stopped = Cell::new(false);
        let delivered = Cell::new(0);
        let result = Motion::new((0, 0), (100, 100), 100).unwrap().run(
            || Duration::ZERO,
            |_| stopped.set(true),
            || {
                if stopped.get() {
                    Err(Error::Emergency)
                } else {
                    Ok(())
                }
            },
            |_, _| {
                delivered.set(delivered.get() + 1);
                Ok(())
            },
        );
        assert!(matches!(result, Err(Error::Emergency)));
        assert_eq!(delivered.get(), 0);
    }

    #[test]
    fn late_scheduler_and_instant_moves_deliver_only_destination() {
        for duration in [0, 100] {
            let points = RefCell::new(Vec::new());
            Motion::new((0, 0), (12, 34), duration)
                .unwrap()
                .run(
                    || Duration::from_millis(150),
                    |_| panic!("no wait expected"),
                    || Ok(()),
                    |x, y| {
                        points.borrow_mut().push((x, y));
                        Ok(())
                    },
                )
                .unwrap();
            assert_eq!(*points.borrow(), vec![(12, 34)]);
        }
    }

    #[test]
    fn full_integer_range_interpolation_does_not_overflow() {
        let motion = Motion::new((i32::MIN, i32::MAX), (i32::MAX, i32::MIN), 100).unwrap();
        assert_eq!(motion.position(Duration::ZERO), (i32::MIN, i32::MAX));
        assert_eq!(
            motion.position(Duration::from_millis(100)),
            (i32::MAX, i32::MIN)
        );
        assert!(Motion::new((0, 0), (1, 1), 2001).is_err());
    }

    #[test]
    fn failed_delivery_is_not_replayed() {
        let attempts = Cell::new(0);
        let result = Motion::new((0, 0), (1, 1), 0).unwrap().run(
            || Duration::ZERO,
            |_| {},
            || Ok(()),
            |_, _| {
                attempts.set(attempts.get() + 1);
                Err(Error::Denied("fixture foreground changed"))
            },
        );
        assert!(result.is_err());
        assert_eq!(attempts.get(), 1);
    }
}
