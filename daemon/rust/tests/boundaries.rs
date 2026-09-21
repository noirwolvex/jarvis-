use jarvis_execution_daemon::{
    capture::{CaptureRing, Frame, ScreenCapture, SimulationCapture, preview_bmp},
    governance::{Capability, EmergencyLatch, Policy, Scope},
    input::{InputController, SimulationInput},
    ipc::{MAX_WIRE_BYTES, Session, read_frame, write_frame},
    process::{ProcessManager, validate_argv},
    types::{Action, ForegroundBinding, MouseButton, Request, now_ms},
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
fn foreground() -> ForegroundBinding {
    ForegroundBinding {
        hwnd: 1001,
        process_id: 42,
        title: "Test window".into(),
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
fn input_capability_authorizes_click_and_keyboard_but_not_capture() {
    let now = now_ms();
    let input = Capability {
        id: "input".into(),
        peer_sha256: "a".repeat(64),
        expires_at_ms: now + 1000,
        scope: Scope::Input { display_id: 0 },
    };
    let policy = Policy::new(true, vec![input], now).unwrap();
    let latch = EmergencyLatch::default();
    let mut req = Request {
        protocol: 1,
        session: Uuid::new_v4(),
        seq: 1,
        expires_at_ms: now + 1000,
        request_id: Uuid::new_v4(),
        capability_id: Some("input".into()),
        action: Action::Click {
            display_id: 0,
            frame_id: Uuid::new_v4(),
            x: 1,
            y: 1,
            foreground: foreground(),
        },
    };
    assert!(policy.authorize(&"a".repeat(64), &req, now, &latch).is_ok());
    req.action = Action::TypeText {
        display_id: 0,
        frame_id: Uuid::new_v4(),
        text: "hello".into(),
        foreground: foreground(),
    };
    assert!(policy.authorize(&"a".repeat(64), &req, now, &latch).is_ok());
    req.action = Action::Hotkey {
        display_id: 0,
        frame_id: Uuid::new_v4(),
        keys: vec!["ctrl".into(), "l".into()],
        foreground: foreground(),
    };
    assert!(policy.authorize(&"a".repeat(64), &req, now, &latch).is_ok());
    req.action = Action::ClickButton {
        display_id: 0,
        frame_id: Uuid::new_v4(),
        x: 1,
        y: 1,
        button: MouseButton::Right,
        clicks: 2,
        foreground: foreground(),
    };
    assert!(policy.authorize(&"a".repeat(64), &req, now, &latch).is_ok());
    req.action = Action::Capture { display_id: 0 };
    assert!(
        policy
            .authorize(&"a".repeat(64), &req, now, &latch)
            .is_err()
    );
}

#[test]
fn capability_max_ttl_and_duplicate_identifiers_are_rejected() {
    let now = now_ms();
    let mut allowed = grant(now);
    allowed.expires_at_ms = now + 24 * 60 * 60 * 1000;
    assert!(Policy::new(true, vec![allowed], now).is_ok());
    let mut cap = grant(now);
    cap.expires_at_ms = now + 24 * 60 * 60 * 1000 + 1;
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
    assert!(
        SimulationInput
            .click(&frame, 1, 1, &foreground(), &clone)
            .is_err()
    );
}

#[test]
fn stale_frames_out_of_display_coordinates_and_invalid_pixels_are_rejected() {
    let mut frame = SimulationCapture.capture(0, 16_384).unwrap();
    assert!(
        SimulationInput
            .click(&frame, 64, 0, &foreground(), &EmergencyLatch::default())
            .is_err()
    );
    assert!(
        SimulationInput
            .click(&frame, -1, 0, &foreground(), &EmergencyLatch::default())
            .is_err()
    );
    assert!(
        SimulationInput
            .type_text(
                &frame,
                &"x".repeat(4097),
                &foreground(),
                &EmergencyLatch::default()
            )
            .is_err()
    );
    assert!(
        SimulationInput
            .click_button(
                &frame,
                1,
                1,
                MouseButton::Right,
                4,
                &foreground(),
                &EmergencyLatch::default()
            )
            .is_err()
    );
    assert!(
        SimulationInput
            .hotkey(
                &frame,
                &vec!["ctrl".into(); 9],
                &foreground(),
                &EmergencyLatch::default()
            )
            .is_err()
    );
    assert!(
        SimulationInput
            .drag(
                &frame,
                1,
                1,
                2,
                2,
                2_001,
                MouseButton::Left,
                &foreground(),
                &EmergencyLatch::default()
            )
            .is_err()
    );
    frame.captured_at = Instant::now() - Duration::from_secs(2);
    assert!(
        SimulationInput
            .click(&frame, 1, 1, &foreground(), &EmergencyLatch::default())
            .is_err()
    );
    assert!(Frame::new(frame.display, vec![1], true, 16_384).is_err());
}

#[test]
fn capture_preview_is_bounded_browser_renderable_bmp() {
    let frame = SimulationCapture.capture(0, 16_384).unwrap();
    let preview = preview_bmp(&frame, 320, 180).unwrap();
    assert_eq!(preview.mime, "image/bmp");
    assert!(preview.width <= 320 && preview.height <= 180);
    assert!(preview.base64.starts_with("Qk"));
    assert!(preview.base64.len() < MAX_WIRE_BYTES);
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

#[tokio::test]
async fn outbound_frame_is_one_write_and_oversized_data_never_reaches_transport() {
    use std::{
        pin::Pin,
        task::{Context, Poll},
    };
    use tokio::io::AsyncWrite;
    #[derive(Default)]
    struct Writer {
        bytes: Vec<u8>,
        writes: usize,
    }
    impl AsyncWrite for Writer {
        fn poll_write(
            mut self: Pin<&mut Self>,
            _: &mut Context<'_>,
            bytes: &[u8],
        ) -> Poll<std::io::Result<usize>> {
            self.writes += 1;
            self.bytes.extend_from_slice(bytes);
            Poll::Ready(Ok(bytes.len()))
        }
        fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<std::io::Result<()>> {
            Poll::Ready(Ok(()))
        }
        fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<std::io::Result<()>> {
            Poll::Ready(Ok(()))
        }
    }
    let mut writer = Writer::default();
    let value = serde_json::json!({"type": "result", "ok": true});
    write_frame(&mut writer, &value).await.unwrap();
    assert_eq!(writer.writes, 1);
    let count = u32::from_be_bytes(writer.bytes[..4].try_into().unwrap()) as usize;
    assert_eq!(count, writer.bytes.len() - 4);
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&writer.bytes[4..]).unwrap(),
        value
    );
    let mut rejected = Writer::default();
    assert!(
        write_frame(&mut rejected, &"x".repeat(MAX_WIRE_BYTES))
            .await
            .is_err()
    );
    assert_eq!(rejected.writes, 0);
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
    assert!(serde_json::from_str::<Action>(r#"{"kind":"type_text","display_id":0,"frame_id":"00000000-0000-0000-0000-000000000000","text":"hello"}"#).is_err());
}

#[test]
fn pointer_duration_is_backward_compatible_and_bounded_before_delivery() {
    let raw = serde_json::json!({"kind": "pointer_move", "display_id": 0,
        "frame_id": "00000000-0000-0000-0000-000000000000", "x": 1, "y": 2,
        "foreground": foreground()});
    assert!(matches!(
        serde_json::from_value::<Action>(raw.clone()).unwrap(),
        Action::PointerMove { duration_ms: 0, .. }
    ));
    let mut timed = raw;
    timed["duration_ms"] = serde_json::json!(120);
    assert!(matches!(
        serde_json::from_value::<Action>(timed).unwrap(),
        Action::PointerMove {
            duration_ms: 120,
            ..
        }
    ));
    let frame = SimulationCapture.capture(0, 16_384).unwrap();
    let latch = EmergencyLatch::default();
    assert!(
        SimulationInput
            .pointer_move(&frame, 1, 2, 120, &foreground(), &latch)
            .is_ok()
    );
    assert!(
        SimulationInput
            .pointer_move(&frame, 1, 2, 2_001, &foreground(), &latch)
            .is_err()
    );
    latch.stop();
    assert!(
        SimulationInput
            .pointer_move(&frame, 1, 2, 120, &foreground(), &latch)
            .is_err()
    );
}

#[test]
fn non_ascii_window_titles_use_the_same_character_budget_as_the_python_binding() {
    let frame = SimulationCapture.capture(0, 16_384).unwrap();
    let latch = EmergencyLatch::default();
    let mut binding = foreground();
    binding.title = "ع".repeat(512);
    assert!(
        SimulationInput
            .type_text(&frame, "fixture", &binding, &latch)
            .is_ok()
    );
    binding.title.push('ع');
    assert!(
        SimulationInput
            .type_text(&frame, "fixture", &binding, &latch)
            .is_err()
    );
}
