use jarvis_execution_daemon::{
    capture::{CaptureRing, Frame, ScreenCapture, SimulationCapture},
    governance::{Capability, EmergencyLatch, Policy, Scope},
    input::{InputController, SimulationInput},
    ipc::{MAX_WIRE_BYTES, Session, read_frame},
    process::{ProcessManager, validate_argv},
    types::{Action, Request, now_ms},
};
use std::time::{Duration, Instant};
use tokio::io::AsyncWriteExt;
use uuid::Uuid;

fn request(session: Uuid, action: Action) -> Request {
    Request {
        protocol: 1,
        session,
        seq: 1,
        expires_at_ms: now_ms() + 1000,
        request_id: Uuid::new_v4(),
        capability_id: Some("observe".into()),
        action,
    }
}
fn grant(now: u64) -> Capability {
    Capability {
        id: "observe".into(),
        peer_sha256: "a".repeat(64),
        expires_at_ms: now + 1000,
        scope: Scope::Observe { display_id: 0 },
    }
}

#[test]
fn replay_sequence_session_expiry_and_ttl_are_rejected() {
    let now = now_ms();
    let mut session = Session::new();
    let mut req = request(session.id, Action::Status {});
    assert!(session.validate(&req, now).is_ok());
    assert!(session.validate(&req, now).is_err());
    req.seq = 3;
    assert!(session.validate(&req, now).is_err());
    req.seq = 2;
    req.session = Uuid::new_v4();
    assert!(session.validate(&req, now).is_err());
    req.session = session.id;
    req.expires_at_ms = now;
    assert!(session.validate(&req, now).is_err());
    req.expires_at_ms = now + 30_001;
    assert!(session.validate(&req, now).is_err());
}

#[test]
fn forged_wrong_peer_wrong_scope_and_expired_capabilities_fail_closed() {
    let now = now_ms();
    let policy = Policy::new(true, vec![grant(now)], now).unwrap();
    let latch = EmergencyLatch::default();
    let mut req = request(Uuid::new_v4(), Action::Capture { display_id: 0 });
    assert!(policy.authorize(&"a".repeat(64), &req, now, &latch).is_ok());
    assert!(
        policy
            .authorize(&"b".repeat(64), &req, now, &latch)
            .is_err()
    );
    assert!(
        policy
            .authorize(&"a".repeat(64), &req, now + 1000, &latch)
            .is_err()
    );
    req.action = Action::Capture { display_id: 1 };
    assert!(
        policy
            .authorize(&"a".repeat(64), &req, now, &latch)
            .is_err()
    );
    req.action = Action::RunProcess {
        executable_id: "observe".into(),
        args: vec![],
        timeout_ms: 1,
    };
    assert!(
        policy
            .authorize(&"a".repeat(64), &req, now, &latch)
            .is_err()
    );
    req.capability_id = Some("forged".into());
    assert!(
        policy
            .authorize(&"a".repeat(64), &req, now, &latch)
            .is_err()
    );
}

#[test]
fn capability_max_ttl_and_duplicate_identifiers_are_rejected() {
    let now = now_ms();
    let mut cap = grant(now);
    cap.expires_at_ms = now + 300_001;
    assert!(Policy::new(true, vec![cap], now).is_err());
    assert!(Policy::new(true, vec![grant(now), grant(now)], now).is_err());
}

#[tokio::test]
async fn capability_monotonic_expiry_survives_wall_clock_rollback() {
    let now = now_ms();
    let mut cap = grant(now);
    cap.expires_at_ms = now + 10;
    let policy = Policy::new(true, vec![cap], now).unwrap();
    tokio::time::sleep(Duration::from_millis(20)).await;
    let req = request(Uuid::new_v4(), Action::Capture { display_id: 0 });
    assert!(
        policy
            .authorize(
                &"a".repeat(64),
                &req,
                now - 1000,
                &EmergencyLatch::default()
            )
            .is_err()
    );
}

#[tokio::test]
async fn emergency_is_shared_idempotent_and_cancels_waiters() {
    let latch = EmergencyLatch::default();
    let clone = latch.clone();
    let token = clone.token();
    latch.stop();
    latch.stop();
    assert!(clone.check().is_err());
    tokio::time::timeout(Duration::from_millis(20), token.cancelled())
        .await
        .unwrap();
    let frame = SimulationCapture.capture(0, 16_384).unwrap();
    assert!(SimulationInput.click(&frame, 1, 1, &clone).is_err());
}

#[test]
fn stale_frames_out_of_display_coordinates_and_invalid_pixels_are_rejected() {
    let mut frame = SimulationCapture.capture(0, 16_384).unwrap();
    assert!(
        SimulationInput
            .click(&frame, 64, 0, &EmergencyLatch::default())
            .is_err()
    );
    assert!(
        SimulationInput
            .click(&frame, -1, 0, &EmergencyLatch::default())
            .is_err()
    );
    frame.captured_at = Instant::now() - Duration::from_secs(2);
    assert!(
        SimulationInput
            .click(&frame, 1, 1, &EmergencyLatch::default())
            .is_err()
    );
    assert!(Frame::new(frame.display, vec![1], true, 16_384).is_err());
}

#[test]
fn capture_ring_enforces_count_and_bytes_and_evicts_old_ids() {
    assert!(CaptureRing::new(0, 1).is_err());
    let mut ring = CaptureRing::new(2, 32_768).unwrap();
    let first = ring
        .push(SimulationCapture.capture(0, 16_384).unwrap())
        .unwrap();
    ring.push(SimulationCapture.capture(0, 16_384).unwrap())
        .unwrap();
    ring.push(SimulationCapture.capture(0, 16_384).unwrap())
        .unwrap();
    assert_eq!(ring.len(), 2);
    assert_eq!(ring.bytes(), 32_768);
    assert!(ring.get(first.id).is_err());
    assert!(CaptureRing::new(1, 1).unwrap().push(first).is_err());
}

#[tokio::test]
async fn oversized_frame_is_rejected_before_payload_allocation() {
    let (mut client, mut server) = tokio::io::duplex(8);
    client.write_u32((MAX_WIRE_BYTES + 1) as u32).await.unwrap();
    assert!(read_frame(&mut server).await.is_err());
}

#[test]
fn arbitrary_commands_unknown_json_fields_and_argv_limits_are_rejected() {
    let manager = ProcessManager::new(vec![], false).unwrap();
    assert!(
        manager
            .validate("rm", &["-rf".into(), "/".into()], 100)
            .is_err()
    );
    assert!(validate_argv(&["x".repeat(4097)]).is_err());
    assert!(validate_argv(&["a\0b".into()]).is_err());
    assert!(serde_json::from_str::<Action>(r#"{"kind":"shell","command":"echo hello"}"#).is_err());
    assert!(serde_json::from_str::<Action>(r#"{"kind":"status","admin":true}"#).is_err());
}
