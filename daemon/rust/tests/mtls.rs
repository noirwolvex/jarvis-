mod common;
use common::Pki;
use jarvis_execution_daemon::{
    dispatcher::Dispatcher,
    governance::{Capability, Policy, Scope},
    ipc::{self, read_frame, write_frame},
    process::ProcessManager,
    types::{Action, Reply, Request, now_ms},
};
use sha2::{Digest, Sha256};
use std::{sync::Arc, time::Duration};
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

#[tokio::test]
async fn mtls_status_capture_stop_and_replay_close_connection() {
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
    let dispatcher = Dispatcher::start(
        Policy::new(true, vec![cap], now_ms()).unwrap(),
        ProcessManager::new(vec![], false).unwrap(),
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
    assert!(matches!(reply, Reply::Result { ok: true, .. }));
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
