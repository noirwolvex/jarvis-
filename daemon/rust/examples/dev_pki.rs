//! Generates a disposable development identity, never a production CA.
#[path = "../tests/common/mod.rs"]
mod common;
use sha2::{Digest, Sha256};
use std::{io::Write, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let directory = PathBuf::from(
        std::env::args_os()
            .nth(1)
            .ok_or("usage: cargo run --example dev_pki -- <NEW private directory>")?,
    );
    // Refuse an existing directory so no key or configuration can be overwritten.
    std::fs::create_dir(&directory)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o700))?;
    }
    let pki = common::Pki::new();
    pki.write(&directory);
    let expiry = jarvis_execution_daemon::types::now_ms() + 300_000;
    let fingerprint = format!("{:x}", Sha256::digest(pki.client.der()));
    let config = serde_json::json!({"listen":"127.0.0.1:7443", "server_cert":"server.pem", "server_key":"server-key.pem", "client_ca":"ca.pem", "simulation":true, "native_capture":false, "allow_uncontained_processes":false, "executables":[], "capabilities":[{"id":"dev-observe", "peer_sha256":fingerprint, "expires_at_ms":expiry, "scope":{"kind":"observe","display_id":0}},{"id":"dev-input", "peer_sha256":fingerprint, "expires_at_ms":expiry, "scope":{"kind":"input","display_id":0}}]});
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(directory.join("config.json"))?
        .write_all(&serde_json::to_vec_pretty(&config)?)?;
    println!(
        "Development PKI written; simulation grants expire in five minutes. Windows: restrict this directory's ACL to your user before use. Start jarvis-daemon with its config.json."
    );
    Ok(())
}
