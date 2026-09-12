//! Reference execution foundation. Native backends are experimental and opt-in.
//! This crate does not implement an operating-system sandbox.
pub mod capture;
pub mod dispatcher;
pub mod governance;
pub mod input;
pub mod ipc;
pub mod plugin;
pub mod process;
pub mod types;

pub use types::{Error, Result};
