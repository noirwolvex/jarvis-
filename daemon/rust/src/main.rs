use jarvis_execution_daemon::{
    Error, Result,
    dispatcher::Dispatcher,
    governance::{Capability, Policy},
    ipc,
    process::{ExecutableRule, ProcessManager},
    types::now_ms,
};
use serde::Deserialize;
use std::{net::SocketAddr, path::PathBuf};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    listen: SocketAddr,
    server_cert: PathBuf,
    server_key: PathBuf,
    client_ca: PathBuf,
    #[serde(default = "default_true")]
    simulation: bool,
    #[serde(default)]
    native_capture: bool,
    #[serde(default)]
    native_input: bool,
    #[serde(default)]
    allow_uncontained_processes: bool,
    #[serde(default)]
    capabilities: Vec<Capability>,
    #[serde(default)]
    executables: Vec<ExecutableRule>,
}
fn default_true() -> bool {
    true
}

#[tokio::main(worker_threads = 4)]
async fn main() -> Result<()> {
    let mut args = std::env::args_os().skip(1);
    let path = args
        .next()
        .ok_or(Error::Denied("usage: jarvis-daemon <config.json>"))?;
    if args.next().is_some() {
        return Err(Error::Denied("unexpected argument"));
    }
    let path = PathBuf::from(path).canonicalize()?;
    let parent = path
        .parent()
        .ok_or(Error::Denied("config directory unavailable"))?;
    if std::fs::metadata(&path)?.len() > 128 * 1024 {
        return Err(Error::Limit("configuration bytes"));
    }
    let mut config: Config = serde_json::from_slice(&std::fs::read(&path)?)?;
    for p in [
        &mut config.server_cert,
        &mut config.server_key,
        &mut config.client_ca,
    ] {
        if p.is_relative() {
            *p = parent.join(&*p);
        }
    }
    if !config.listen.ip().is_loopback() {
        return Err(Error::Denied("listen address must be loopback"));
    }
    if config.native_input && config.simulation {
        return Err(Error::Denied(
            "native input requires simulation=false",
        ));
    }
    let tls = ipc::tls_config(&config.server_cert, &config.server_key, &config.client_ca)?;
    let policy = Policy::new(config.simulation, config.capabilities, now_ms())?;
    let processes = ProcessManager::new(config.executables, config.allow_uncontained_processes)?;
    let dispatcher = Dispatcher::start(
        policy,
        processes,
        config.native_capture,
        config.native_input,
    )?;
    let stop = dispatcher.emergency.clone();
    // Local Ctrl-C stops work without consulting a model, planner or dispatch queue.
    tokio::spawn(async move {
        if tokio::signal::ctrl_c().await.is_ok() {
            stop.stop();
        }
    });
    eprintln!(
        "JARVIS reference daemon: {} simulation={} native_capture={} native_input={} sandbox=none",
        config.listen, config.simulation, config.native_capture, config.native_input
    );
    ipc::serve(config.listen, tls, dispatcher).await
}
