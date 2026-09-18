//! Generates a disposable development identity, never a production CA.
#[path = "../tests/common/mod.rs"]
mod common;
use sha2::{Digest, Sha256};
use std::{io::Write, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let directory = PathBuf::from(
        args.next()
            .ok_or("usage: cargo run --example dev_pki -- <NEW private directory> [--native]")?,
    );
    let native = match args.next() {
        None => false,
        Some(value) if value.to_string_lossy() == "--native" => true,
        Some(_) => return Err("second argument must be --native".into()),
    };
    if args.next().is_some() {
        return Err("unexpected extra argument".into());
    }

    // Refuse an existing directory so no key or configuration can be overwritten.
    std::fs::create_dir(&directory)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o700))?;
    }
    let pki = common::Pki::new();
    pki.write(&directory);
    let grant_ms = if native {
        24 * 60 * 60 * 1000
    } else {
        5 * 60 * 1000
    };
    let expiry = jarvis_execution_daemon::types::now_ms() + grant_ms;
    let fingerprint = format!("{:x}", Sha256::digest(pki.client.der()));
    let config = serde_json::json!({
        "listen":"127.0.0.1:7443",
        "server_cert":"server.pem",
        "server_key":"server-key.pem",
        "client_ca":"ca.pem",
        "simulation":!native,
        "native_capture":native,
        "native_input":native,
        "allow_uncontained_processes":false,
        "executables":[],
        "capabilities":[
            {"id":"dev-observe","peer_sha256":fingerprint,"expires_at_ms":expiry,"scope":{"kind":"observe","display_id":0}},
            {"id":"dev-input","peer_sha256":fingerprint,"expires_at_ms":expiry,"scope":{"kind":"input","display_id":0}}
        ]
    });
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(directory.join("config.json"))?
        .write_all(&serde_json::to_vec_pretty(&config)?)?;
    if native {
        println!(
            "Development PKI + native engine config written; grants expire in twenty-four hours. Verify the actual Rust display id before use on multi-monitor systems. Windows: restrict this directory's ACL to your user."
        );
    } else {
        println!(
            "Development PKI written; simulation grants expire in five minutes. Windows: restrict this directory's ACL to your user before use."
        );
    }
    Ok(())
}
