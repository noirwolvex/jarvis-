//! Plugins propose typed actions; they never receive a ProcessManager or native handles.
//! In-process Rust plugins remain trusted code: this trait is not a sandbox.
use crate::{Result, types::Action};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct PluginManifest {
    pub id: String,
    pub version: String,
    pub protocol: u8,
    pub requested_scopes: Vec<crate::governance::Scope>,
}
pub trait ProposalPlugin: Send + Sync {
    fn manifest(&self) -> PluginManifest;
    fn propose(&self, bounded_observation: &serde_json::Value) -> Result<Vec<Action>>;
}
pub fn checked_proposal(
    plugin: &dyn ProposalPlugin,
    observation: &serde_json::Value,
) -> Result<Vec<Action>> {
    if serde_json::to_vec(observation)?.len() > 64 * 1024 {
        return Err(crate::Error::Limit("plugin observation"));
    }
    if plugin.manifest().protocol != 1 {
        return Err(crate::Error::Protocol("plugin version"));
    }
    let proposals = plugin.propose(observation)?;
    if proposals.len() > 32 {
        return Err(crate::Error::Limit("plugin proposals"));
    }
    // The orchestrator must route these back through authenticated capability
    // dispatch. Returning a proposal confers no authority to execute it.
    Ok(proposals)
}
