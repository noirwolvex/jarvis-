mod common;
use common::Pki;
use jarvis_execution_daemon::{
    dispatcher::Dispatcher,
    governance::{Capability, EmergencyLatch, Policy, Scope},
    ipc::{self, read_frame, write_frame},
    process::ProcessManager,
    types::{Action, ForegroundBinding, Reply, Request, now_ms},
};
use sha2::{Digest, Sha256};
use std::{net::SocketAddr, sync::Arc, time::Duration};
use tokio::net::{TcpListener, TcpStream};
use tokio_rustls::{
    TlsConnector,
    rustls::{
        self, ClientConfig, RootCertStore,
        pki_types::{PrivatePkcs8KeyDer, ServerName},
    },
};
use uuid::Uuid;

fn client_config(pki: &Pki, authenticated: bool) -> Arc<ClientConfig> {
    let mut roots = RootCertStore::empty();
    roots.add(pki.ca.der().clone()).unwrap();
    let builder =
        ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_protocol_versions(&[&rustls::version::TLS13])
            .unwrap()
            .with_root_certificates(roots);
    Arc::new(if authenticated {
        builder
            .with_client_auth_cert(
                vec![pki.client.der().clone()],
                PrivatePkcs8KeyDer::from(pki.client_key.serialize_der()).into(),
            )
            .unwrap()
    } else {
        builder.with_no_client_auth()
    })
}

type ClientStream = tokio_rustls::client::TlsStream<TcpStream>;

struct SaturatedServer {
    pki: Pki,
    address: SocketAddr,
    latch: EmergencyLatch,
    server: tokio::task::JoinHandle<jarvis_execution_daemon::Result<()>>,
    _normal_sessions: Vec<ClientStream>,
}

impl SaturatedServer {
    async fn new() -> Self {
        let pki = Pki::new();
        let directory = tempfile::tempdir().unwrap();
        pki.write(directory.path());
        let tls = ipc::tls_config(
            &directory.path().join("server.pem"),
            &directory.path().join("server-key.pem"),
            &directory.path().join("ca.pem"),
        )
        .unwrap();
        let dispatcher = Dispatcher::start(
            Policy::new(true, vec![], now_ms()).unwrap(),
            ProcessManager::new(vec![], false).unwrap(),
            false,
            false,
        )
        .unwrap();
        let latch = dispatcher.emergency.clone();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(ipc::serve_listener(listener, tls, dispatcher));
        let mut fixture = Self {
            pki,
            address,
            latch,
            server,
            _normal_sessions: Vec::new(),
        };
        for _ in 0..ipc::MAX_CONNECTIONS {
            let (stream, _) = fixture.connect().await;
            fixture._normal_sessions.push(stream);
        }
        fixture
    }

    async fn connect(&self) -> (ClientStream, Request) {
        tokio::time::timeout(Duration::from_secs(3), async {
            let mut stream = TlsConnector::from(client_config(&self.pki, true))
                .connect(
                    ServerName::try_from("localhost").unwrap(),
                    TcpStream::connect(self.address).await.unwrap(),
                )
                .await
                .unwrap();
            let hello: Reply =
                serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
            let Reply::Hello { session, .. } = hello else {
                panic!("expected authenticated hello")
            };
            let request = Request {
                protocol: 1,
                session,
                seq: 1,
                expires_at_ms: now_ms() + 5000,
                request_id: Uuid::new_v4(),
                capability_id: None,
                action: Action::EmergencyStop {},
            };
            (stream, request)
        })
        .await
        .expect("connection timed out")
    }
}

impl Drop for SaturatedServer {
    fn drop(&mut self) {
        self.server.abort();
    }
}

async fn assert_closed(stream: &mut ClientStream) {
    assert!(
        tokio::time::timeout(Duration::from_secs(2), read_frame(stream))
            .await
            .expect("connection did not close")
            .is_err()
    );
}

async fn stop_reserved(fixture: &SaturatedServer) {
    let (mut stream, request) = fixture.connect().await;
    write_frame(&mut stream, &request).await.unwrap();
    let reply: Reply = serde_json::from_slice(
        &tokio::time::timeout(Duration::from_secs(2), read_frame(&mut stream))
            .await
            .expect("emergency stop response timed out")
            .unwrap(),
    )
    .unwrap();
    let Reply::Result { ok: true, data, .. } = reply else {
        panic!("expected stop acknowledgement")
    };
    assert_eq!(data["emergency_stopped"], true);
    assert_eq!(data["restart_required"], true);
    assert!(fixture.latch.check().is_err());
    assert_closed(&mut stream).await;
}

#[tokio::test]
async fn emergency_stop_is_admitted_when_all_normal_sessions_are_occupied() {
    let fixture = SaturatedServer::new().await;
    stop_reserved(&fixture).await;
}

#[tokio::test]
async fn reserved_session_cannot_dispatch_other_actions_or_accept_invalid_stop_envelopes() {
    let fixture = SaturatedServer::new().await;
    for action in [
        Action::Status {},
        Action::Capture { display_id: 0 },
        Action::TypeText {
            display_id: 0,
            frame_id: Uuid::new_v4(),
            text: "never dispatched".into(),
            foreground: ForegroundBinding {
                hwnd: 1001,
                process_id: 42,
                title: "Simulation input fixture".into(),
            },
        },
    ] {
        let (mut stream, mut request) = fixture.connect().await;
        request.action = action;
        write_frame(&mut stream, &request).await.unwrap();
        let reply: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
        let Reply::Result {
            ok: false, data, ..
        } = reply
        else {
            panic!("reserved session admitted a regular action")
        };
        assert!(
            data["error"]
                .as_str()
                .unwrap()
                .contains("reserved for emergency stop")
        );
        assert_closed(&mut stream).await;
        assert!(fixture.latch.check().is_ok());
    }
    for invalid_field in ["protocol", "session", "sequence", "expiry"] {
        let (mut stream, mut request) = fixture.connect().await;
        match invalid_field {
            "protocol" => request.protocol = 2,
            "session" => request.session = Uuid::new_v4(),
            "sequence" => request.seq = 2,
            "expiry" => request.expires_at_ms = now_ms().saturating_sub(1),
            _ => unreachable!(),
        }
        write_frame(&mut stream, &request).await.unwrap();
        assert_closed(&mut stream).await;
        assert!(fixture.latch.check().is_ok());
    }
    stop_reserved(&fixture).await;
}

#[tokio::test]
async fn reserved_admission_is_bounded_and_idle_session_releases_its_slot() {
    let fixture = SaturatedServer::new().await;
    let (mut idle, _) = fixture.connect().await;
    // Four normal sessions plus one reserved slot is the absolute admission cap.
    let excess = tokio::time::timeout(Duration::from_secs(2), async {
        TlsConnector::from(client_config(&fixture.pki, true))
            .connect(
                ServerName::try_from("localhost").unwrap(),
                TcpStream::connect(fixture.address).await.unwrap(),
            )
            .await
    })
    .await
    .expect("excess connection was not rejected promptly");
    if let Ok(mut stream) = excess {
        assert_closed(&mut stream).await;
    }
    assert_closed(&mut idle).await;
    assert!(fixture.latch.check().is_ok());
    stop_reserved(&fixture).await;
}

#[tokio::test]
async fn reserved_stop_admission_still_requires_mutual_tls() {
    let fixture = SaturatedServer::new().await;
    let anonymous = TlsConnector::from(client_config(&fixture.pki, false))
        .connect(
            ServerName::try_from("localhost").unwrap(),
            TcpStream::connect(fixture.address).await.unwrap(),
        )
        .await;
    if let Ok(mut stream) = anonymous {
        assert_closed(&mut stream).await;
    }
    assert!(fixture.latch.check().is_ok());
    stop_reserved(&fixture).await;
}

#[tokio::test]
async fn generated_development_identity_works_with_strict_python_openssl() {
    let pki = Pki::new();
    let directory = tempfile::tempdir().unwrap();
    pki.write(directory.path());
    let tls = ipc::tls_config(
        &directory.path().join("server.pem"),
        &directory.path().join("server-key.pem"),
        &directory.path().join("ca.pem"),
    )
    .unwrap();
    let dispatcher = Dispatcher::start(
        Policy::new(true, vec![], now_ms()).unwrap(),
        ProcessManager::new(vec![], false).unwrap(),
        false,
        false,
    )
    .unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(ipc::serve_listener(listener, tls, dispatcher));
    let result = tokio::time::timeout(
        Duration::from_secs(15),
        tokio::process::Command::new(
            std::env::var("JARVIS_TEST_PYTHON").unwrap_or_else(|_| "python".into()),
        )
        .arg("-c")
        .arg(include_str!("python_tls_probe.py"))
        .arg(directory.path())
        .arg(address.port().to_string())
        .kill_on_drop(true)
        .output(),
    )
    .await;
    server.abort();
    let output = result
        .expect("Python TLS probe timed out")
        .expect("Python 3.11+ is required for the mTLS interoperability test");
    assert!(
        output.status.success(),
        "Python/OpenSSL rejected the development chain: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).trim(),
        "TLSv1.3 status verified"
    );
}

#[tokio::test]
async fn mtls_capture_timed_input_stop_and_replay_close_connection() {
    let pki = Pki::new();
    let directory = tempfile::tempdir().unwrap();
    pki.write(directory.path());
    let tls = ipc::tls_config(
        &directory.path().join("server.pem"),
        &directory.path().join("server-key.pem"),
        &directory.path().join("ca.pem"),
    )
    .unwrap();
    let cap = Capability {
        id: "observe".into(),
        peer_sha256: format!("{:x}", Sha256::digest(pki.client.der())),
        expires_at_ms: now_ms() + 60_000,
        scope: Scope::Observe { display_id: 0 },
    };
    let input_cap = Capability {
        id: "input".into(),
        scope: Scope::Input { display_id: 0 },
        ..cap.clone()
    };
    let dispatcher = Dispatcher::start(
        Policy::new(true, vec![cap, input_cap], now_ms()).unwrap(),
        ProcessManager::new(vec![], false).unwrap(),
        false,
        false,
    )
    .unwrap();
    let latch = dispatcher.emergency.clone();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(ipc::serve_listener(listener, tls, dispatcher));

    // TLS 1.3 may finish the client side of the handshake before receiving the
    // server's certificate-required alert; either connect or first read must fail.
    let anonymous = TlsConnector::from(client_config(&pki, false))
        .connect(
            ServerName::try_from("localhost").unwrap(),
            TcpStream::connect(address).await.unwrap(),
        )
        .await;
    if let Ok(mut stream) = anonymous {
        assert!(
            tokio::time::timeout(Duration::from_secs(2), read_frame(&mut stream))
                .await
                .unwrap()
                .is_err()
        );
    }

    let mut stream = TlsConnector::from(client_config(&pki, true))
        .connect(
            ServerName::try_from("localhost").unwrap(),
            TcpStream::connect(address).await.unwrap(),
        )
        .await
        .unwrap();
    let hello: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
    let Reply::Hello { session, .. } = hello else {
        panic!("expected hello")
    };
    let mut req = Request {
        protocol: 1,
        session,
        seq: 1,
        expires_at_ms: now_ms() + 5000,
        request_id: Uuid::new_v4(),
        capability_id: None,
        action: Action::Status {},
    };
    write_frame(&mut stream, &req).await.unwrap();
    let reply: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
    assert!(matches!(reply, Reply::Result { ok: true, .. }));
    req.seq += 1;
    req.request_id = Uuid::new_v4();
    req.action = Action::Capture { display_id: 0 };
    // Authenticated identity still cannot observe without a capability.
    write_frame(&mut stream, &req).await.unwrap();
    let reply: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
    assert!(matches!(reply, Reply::Result { ok: false, .. }));
    req.seq += 1;
    req.capability_id = Some("observe".into());
    write_frame(&mut stream, &req).await.unwrap();
    let reply: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
    let Reply::Result { ok: true, data, .. } = reply else {
        panic!("expected authorized capture")
    };
    let frame_id = serde_json::from_value(data["frame"]["id"].clone()).unwrap();
    let foreground = ForegroundBinding {
        hwnd: 1001,
        process_id: 42,
        title: "Simulation input fixture".into(),
    };
    req.seq += 1;
    req.request_id = Uuid::new_v4();
    req.action = Action::PointerMove {
        display_id: 0,
        frame_id,
        x: 1,
        y: 2,
        duration_ms: 80,
        foreground: foreground.clone(),
    };
    // Observation authority cannot authorize a mutation, even in simulation.
    write_frame(&mut stream, &req).await.unwrap();
    let reply: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
    assert!(matches!(reply, Reply::Result { ok: false, .. }));
    req.seq += 1;
    req.request_id = Uuid::new_v4();
    req.capability_id = Some("input".into());
    write_frame(&mut stream, &req).await.unwrap();
    let reply: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
    let Reply::Result { ok: true, data, .. } = reply else {
        panic!("expected authorized simulated movement")
    };
    assert_eq!(data["pointer"]["duration_ms"], 80);
    assert_eq!(data["simulation"], true);
    assert_eq!(data["executed"], false);
    assert_eq!(data["verified"], false);
    req.seq += 1;
    req.request_id = Uuid::new_v4();
    req.action = Action::TypeText {
        display_id: 0,
        frame_id,
        text: "ع🦀".repeat(2048),
        foreground,
    };
    write_frame(&mut stream, &req).await.unwrap();
    let reply: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
    let Reply::Result { ok: true, data, .. } = reply else {
        panic!("expected authorized simulated Unicode input")
    };
    assert_eq!(data["characters"], 4096);
    assert_eq!(data["executed"], false);
    assert_eq!(data["verified"], false);
    req.seq += 1;
    req.action = Action::EmergencyStop {};
    req.capability_id = None;
    write_frame(&mut stream, &req).await.unwrap();
    let reply: Reply = serde_json::from_slice(&read_frame(&mut stream).await.unwrap()).unwrap();
    assert!(matches!(reply, Reply::Result { ok: true, .. }));
    assert!(latch.check().is_err());
    // A duplicate authenticated envelope must terminate the session.
    write_frame(&mut stream, &req).await.unwrap();
    assert!(
        tokio::time::timeout(Duration::from_secs(2), read_frame(&mut stream))
            .await
            .unwrap()
            .is_err()
    );
    server.abort();
}
