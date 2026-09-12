use jarvis_execution_daemon::{governance::EmergencyLatch, process::{ExecutableRule, MAX_OUTPUT_BYTES, ProcessManager, file_sha256}, types::Reply};
use std::time::Duration;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

fn manager(fixture: &str, allow: bool) -> (ProcessManager, Vec<String>) {
    let path = std::env::current_exe().unwrap();
    let args = vec!["--ignored".into(), "--exact".into(), fixture.into(), "--nocapture".into()];
    let rule = ExecutableRule { id: "fixture".into(), sha256: file_sha256(&path).unwrap(), working_directory: path.parent().unwrap().to_path_buf(), path, allowed_argv: vec![args.clone()] };
    (ProcessManager::new(vec![rule], allow).unwrap(), args)
}

#[test]
#[ignore = "subprocess fixture; invoked by process boundary tests"]
fn fixture_ok() { println!("fixture stdout"); eprintln!("fixture stderr"); }
#[test]
#[ignore = "subprocess fixture; invoked by process boundary tests"]
fn fixture_sleep() { std::thread::sleep(Duration::from_secs(5)); }
#[test]
#[ignore = "subprocess fixture; invoked by process boundary tests"]
fn fixture_flood() { use std::io::Write; let _ = std::io::stdout().write_all(&vec![b'x'; MAX_OUTPUT_BYTES*4]); }

#[tokio::test]
async fn process_success_streams_both_outputs_and_verifies_exit() {
    let (manager, args) = manager("fixture_ok", true);
    let (tx, mut rx) = mpsc::channel(32);
    let result = manager.run("fixture", &args, 2000, Uuid::new_v4(), EmergencyLatch::default(), CancellationToken::new(), tx).await.unwrap();
    assert!(result.verified);
    let mut streams = vec![];
    while let Some(Reply::Output { stream, .. }) = rx.recv().await { streams.push(stream); }
    assert!(streams.iter().any(|s| s == "stdout"));
    assert!(streams.iter().any(|s| s == "stderr"));
}

#[tokio::test]
async fn process_launch_requires_exact_argv_and_containment_opt_in() {
    let (manager, args) = manager("fixture_ok", false);
    assert!(manager.validate("fixture", &["--help".into()], 100).is_err());
    let (tx, _) = mpsc::channel(32);
    assert!(manager.run("fixture", &args, 100, Uuid::new_v4(), EmergencyLatch::default(), CancellationToken::new(), tx).await.is_err());
}

#[tokio::test]
async fn process_timeout_kills_direct_child_and_returns_unverified() {
    let (manager, args) = manager("fixture_sleep", true);
    let (tx, _rx) = mpsc::channel(32);
    let result = manager.run("fixture", &args, 50, Uuid::new_v4(), EmergencyLatch::default(), CancellationToken::new(), tx).await.unwrap();
    assert_eq!(result.termination, "timeout");
    assert!(!result.verified);
}

#[tokio::test]
async fn emergency_interrupts_running_process_without_dispatch() {
    let (manager, args) = manager("fixture_sleep", true);
    let latch = EmergencyLatch::default();
    let stop = latch.clone();
    tokio::spawn(async move { tokio::time::sleep(Duration::from_millis(50)).await; stop.stop(); });
    let (tx, _rx) = mpsc::channel(32);
    let result = manager.run("fixture", &args, 2000, Uuid::new_v4(), latch, CancellationToken::new(), tx).await.unwrap();
    assert_eq!(result.termination, "emergency_stop");
    assert!(!result.verified);
}

#[tokio::test]
async fn output_flood_is_bounded_and_never_verified() {
    let (manager, args) = manager("fixture_flood", true);
    let (tx, _rx) = mpsc::channel(32);
    let result = manager.run("fixture", &args, 2000, Uuid::new_v4(), EmergencyLatch::default(), CancellationToken::new(), tx).await.unwrap();
    assert_eq!(result.termination, "output_limit");
    assert!(result.output_bytes <= MAX_OUTPUT_BYTES);
    assert!(!result.verified);
}
