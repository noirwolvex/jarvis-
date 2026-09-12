//! Admission policy for a caller-supplied measured sample. This is not an OS
//! resource monitor or an OS-enforced process quota. Missing/stale data denies work.
use crate::{Error, Result};
use std::time::{Duration, Instant};

pub struct ResourceSample {
    pub observed_at: Instant,
    pub ram_fraction: f32,
    pub vram_fraction: f32,
    pub cpu_fraction: f32,
    pub disk_free_bytes: u64,
    pub thermal_pressure: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Admission {
    Normal,
    Throttle,
    ObservationOnly,
}

pub struct ResourceGovernor {
    healthy_samples: u8,
    mode: Admission,
}
impl Default for ResourceGovernor {
    fn default() -> Self {
        Self {
            healthy_samples: 0,
            mode: Admission::ObservationOnly,
        }
    }
}
impl ResourceGovernor {
    pub fn evaluate(&mut self, sample: &ResourceSample, now: Instant) -> Result<Admission> {
        if !now
            .checked_duration_since(sample.observed_at)
            .is_some_and(|age| age < Duration::from_secs(2))
        {
            self.mode = Admission::ObservationOnly;
            self.healthy_samples = 0;
            return Err(Error::Stale);
        }
        if [
            sample.ram_fraction,
            sample.vram_fraction,
            sample.cpu_fraction,
        ]
        .iter()
        .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
        {
            self.mode = Admission::ObservationOnly;
            self.healthy_samples = 0;
            return Err(Error::Denied("invalid resource measurement"));
        }
        if sample.ram_fraction >= 0.90
            || sample.vram_fraction >= 0.90
            || sample.thermal_pressure
            || sample.disk_free_bytes < 5 * 1024 * 1024 * 1024
        {
            self.mode = Admission::ObservationOnly;
            self.healthy_samples = 0;
        } else if sample.ram_fraction >= 0.85
            || sample.vram_fraction >= 0.85
            || sample.cpu_fraction >= 0.90
        {
            self.mode = Admission::Throttle;
            self.healthy_samples = 0;
        } else if sample.ram_fraction < 0.75
            && sample.vram_fraction < 0.75
            && sample.cpu_fraction < 0.80
        {
            self.healthy_samples = self.healthy_samples.saturating_add(1);
            if self.healthy_samples >= 3 {
                self.mode = Admission::Normal;
            }
        } else {
            self.healthy_samples = 0;
        }
        Ok(self.mode)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pressure_requires_three_healthy_samples_before_readmission() {
        let now = Instant::now();
        let mut governor = ResourceGovernor::default();
        let mut sample = ResourceSample {
            observed_at: now,
            ram_fraction: 0.95,
            vram_fraction: 0.5,
            cpu_fraction: 0.5,
            disk_free_bytes: 8 * 1024 * 1024 * 1024,
            thermal_pressure: false,
        };
        assert_eq!(
            governor.evaluate(&sample, now).unwrap(),
            Admission::ObservationOnly
        );
        sample.ram_fraction = 0.5;
        assert_eq!(
            governor.evaluate(&sample, now).unwrap(),
            Admission::ObservationOnly
        );
        assert_eq!(
            governor.evaluate(&sample, now).unwrap(),
            Admission::ObservationOnly
        );
        assert_eq!(governor.evaluate(&sample, now).unwrap(), Admission::Normal);
        sample.cpu_fraction = 0.95;
        assert_eq!(
            governor.evaluate(&sample, now).unwrap(),
            Admission::Throttle
        );
    }
    #[test]
    fn stale_or_invalid_samples_fail_closed() {
        let now = Instant::now();
        let mut governor = ResourceGovernor::default();
        let mut sample = ResourceSample {
            observed_at: now - Duration::from_secs(3),
            ram_fraction: 0.5,
            vram_fraction: 0.5,
            cpu_fraction: 0.5,
            disk_free_bytes: u64::MAX,
            thermal_pressure: false,
        };
        assert!(governor.evaluate(&sample, now).is_err());
        sample.observed_at = now;
        sample.ram_fraction = f32::NAN;
        assert!(governor.evaluate(&sample, now).is_err());
    }
}
