use crate::{
    Error, Result,
    dispatcher::{Dispatcher, Job, REPLY_CAPACITY},
    governance::MAX_REQUEST_TTL_MS,
    types::{Action, Reply, Request, now_ms},
};
use sha2::{Digest, Sha256};
use std::{
    io::BufReader,
    net::SocketAddr,
    path::Path,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::{Semaphore, mpsc},
};
use tokio_rustls::{
    TlsAcceptor,
    rustls::{self, RootCertStore, ServerConfig, pki_types::CertificateDer},
};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

pub const MAX_WIRE_BYTES: usize = 256 * 1024;
pub const MAX_CONNECTIONS: usize = 4;

pub struct Session {
    pub id: Uuid,
    sequence: u64,
    last_now_ms: u64,
}
impl Default for Session {
    fn default() -> Self {
        Self::new()
    }
}
impl Session {
    pub fn new() -> Self {
        Self {
            id: Uuid::new_v4(),
            sequence: 0,
            last_now_ms: now_ms(),
        }
    }
    pub fn validate(&mut self, request: &Request, now: u64) -> Result<Instant> {
        // Clock rollback never makes an already-seen timestamp fresh again.
        let now = now.max(self.last_now_ms);
        self.last_now_ms = now;
        if request.protocol != 1 || request.session != self.id {
            return Err(Error::Protocol("version or session mismatch"));
        }
        if self.sequence.checked_add(1) != Some(request.seq) {
            return Err(Error::Protocol("replayed or out-of-order sequence"));
        }
        let ttl = request
            .expires_at_ms
            .checked_sub(now)
            .filter(|ttl| *ttl > 0 && *ttl <= MAX_REQUEST_TTL_MS)
            .ok_or(Error::Stale)?;
        if request
            .capability_id
            .as_ref()
            .is_some_and(|id| id.len() > 128)
        {
            return Err(Error::Limit("capability id"));
        }
        self.sequence = request.seq;
        Ok(Instant::now() + Duration::from_millis(ttl))
    }
}

pub async fn read_frame<R: AsyncRead + Unpin>(reader: &mut R) -> Result<Vec<u8>> {
    let length = reader.read_u32().await? as usize;
    if length == 0 || length > MAX_WIRE_BYTES {
        return Err(Error::Limit("IPC frame bytes"));
    }
    let mut data = vec![0; length];
    reader.read_exact(&mut data).await?;
    Ok(data)
}
pub async fn write_frame<W: AsyncWrite + Unpin, T: serde::Serialize>(
    writer: &mut W,
    value: &T,
) -> Result<()> {
    let data = serde_json::to_vec(value)?;
    if data.len() > MAX_WIRE_BYTES {
        return Err(Error::Limit("IPC reply bytes"));
    }
    writer.write_u32(data.len() as u32).await?;
    writer.write_all(&data).await?;
    writer.flush().await?;
    Ok(())
}

pub fn certificates(path: &Path) -> Result<Vec<CertificateDer<'static>>> {
    let mut reader = BufReader::new(std::fs::File::open(path)?);
    let certs: Vec<_> = rustls_pemfile::certs(&mut reader).collect::<std::io::Result<_>>()?;
    if certs.is_empty() || certs.len() > 8 {
        return Err(Error::Denied("invalid certificate chain"));
    }
    Ok(certs)
}
pub fn tls_config(
    server_cert: &Path,
    server_key: &Path,
    client_ca: &Path,
) -> Result<Arc<ServerConfig>> {
    let certs = certificates(server_cert)?;
    let mut reader = BufReader::new(std::fs::File::open(server_key)?);
    let key = rustls_pemfile::private_key(&mut reader)?.ok_or(Error::Denied("missing TLS key"))?;
    let mut roots = RootCertStore::empty();
    for cert in certificates(client_ca)? {
        roots
            .add(cert)
            .map_err(|_| Error::Denied("invalid client trust root"))?;
    }
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let verifier = rustls::server::WebPkiClientVerifier::builder_with_provider(
        Arc::new(roots),
        provider.clone(),
    )
    .build()
    .map_err(|_| Error::Denied("invalid client verifier"))?;
    let mut config = ServerConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13])
        .map_err(|_| Error::Denied("TLS version unavailable"))?
        .with_client_cert_verifier(verifier)
        .with_single_cert(certs, key)
        .map_err(|_| Error::Denied("invalid TLS identity"))?;
    config.max_early_data_size = 0;
    config.send_tls13_tickets = 0;
    config.alpn_protocols = vec![b"jarvis-execution/1".to_vec()];
    Ok(Arc::new(config))
}

pub async fn serve(
    address: SocketAddr,
    tls: Arc<ServerConfig>,
    dispatcher: Dispatcher,
) -> Result<()> {
    if !address.ip().is_loopback() {
        return Err(Error::Denied("IPC must bind loopback"));
    }
    let listener = TcpListener::bind(address).await?;
    serve_listener(listener, tls, dispatcher).await
}
pub async fn serve_listener(
    listener: TcpListener,
    tls: Arc<ServerConfig>,
    dispatcher: Dispatcher,
) -> Result<()> {
    if !listener.local_addr()?.ip().is_loopback() {
        return Err(Error::Denied("IPC must bind loopback"));
    }
    let connections = Arc::new(Semaphore::new(MAX_CONNECTIONS));
    let acceptor = TlsAcceptor::from(tls);
    loop {
        let (socket, peer) = listener.accept().await?;
        if !peer.ip().is_loopback() {
            continue;
        }
        let Ok(permit) = connections.clone().try_acquire_owned() else {
            continue;
        };
        let acceptor = acceptor.clone();
        let dispatcher = dispatcher.clone();
        tokio::spawn(async move {
            let _permit = permit;
            // Authentication failures are deliberately not reflected as plaintext.
            let _ = tokio::time::timeout(
                Duration::from_secs(300),
                connection(socket, acceptor, dispatcher),
            )
            .await;
        });
    }
}

async fn connection(
    socket: TcpStream,
    acceptor: TlsAcceptor,
    dispatcher: Dispatcher,
) -> Result<()> {
    let mut stream = tokio::time::timeout(Duration::from_secs(5), acceptor.accept(socket))
        .await
        .map_err(|_| Error::Protocol("TLS handshake timeout"))??;
    let cert = stream
        .get_ref()
        .1
        .peer_certificates()
        .and_then(|chain| chain.first())
        .ok_or(Error::Denied("client certificate required"))?;
    let peer = format!("{:x}", Sha256::digest(cert.as_ref()));
    let mut session = Session::new();
    write_frame(
        &mut stream,
        &Reply::Hello {
            protocol: 1,
            session: session.id,
            max_frame_bytes: MAX_WIRE_BYTES,
            simulation: dispatcher.simulation,
        },
    )
    .await?;
    let (mut reader, mut writer) = tokio::io::split(stream);
    let (outbound, mut replies) = mpsc::channel::<Reply>(REPLY_CAPACITY);
    let disconnected = CancellationToken::new();
    let _disconnect_guard = disconnected.clone().drop_guard();
    // Keep frame parsing in a single future: cancelling a partial read and reusing
    // the stream would corrupt length-prefix alignment.
    let read_loop = async {
        for _ in 0..256 {
            let bytes = tokio::time::timeout(Duration::from_secs(30), read_frame(&mut reader))
                .await
                .map_err(|_| Error::Protocol("request idle timeout"))??;
            let request: Request = serde_json::from_slice(&bytes)?;
            let deadline = session.validate(&request, now_ms())?;
            let id = request.request_id;
            if matches!(request.action, Action::EmergencyStop {}) {
                dispatcher.emergency.stop();
                outbound.try_send(Reply::result(id, Ok(serde_json::json!({"emergency_stopped": true, "restart_required": true})))).map_err(|_| Error::Limit("reply queue full"))?;
                continue;
            }
            let job = Job {
                peer: peer.clone(),
                request,
                reply: outbound.clone(),
                disconnected: disconnected.clone(),
                deadline,
            };
            if let Err(error) = dispatcher.submit(job) {
                outbound
                    .try_send(Reply::result(id, Err(error)))
                    .map_err(|_| Error::Limit("reply queue full"))?;
            }
        }
        Err::<(), Error>(Error::Limit("session request count"))
    };
    let write_loop = async {
        while let Some(reply) = replies.recv().await {
            tokio::time::timeout(Duration::from_secs(2), write_frame(&mut writer, &reply))
                .await
                .map_err(|_| Error::Protocol("reply write timeout"))??;
        }
        Ok(())
    };
    tokio::select! { result = read_loop => result, result = write_loop => result }
}
