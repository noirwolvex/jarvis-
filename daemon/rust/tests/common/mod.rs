use rcgen::{
    BasicConstraints, Certificate, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer,
    KeyPair, KeyUsagePurpose,
};

pub struct Pki {
    pub ca: Certificate,
    pub server: Certificate,
    pub server_key: KeyPair,
    pub client: Certificate,
    pub client_key: KeyPair,
}
impl Pki {
    pub fn new() -> Self {
        let ca_key = KeyPair::generate().unwrap();
        let mut params = CertificateParams::new(vec!["jarvis-test-ca".into()]).unwrap();
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let ca = params.self_signed(&ca_key).unwrap();
        let issuer = Issuer::new(params, ca_key);
        let server_key = KeyPair::generate().unwrap();
        let mut server_params =
            CertificateParams::new(vec!["localhost".into(), "127.0.0.1".into()]).unwrap();
        server_params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
        let server = server_params.signed_by(&server_key, &issuer).unwrap();
        let client_key = KeyPair::generate().unwrap();
        let mut client_params = CertificateParams::new(vec!["jarvis-test-client".into()]).unwrap();
        client_params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ClientAuth];
        let client = client_params.signed_by(&client_key, &issuer).unwrap();
        Self {
            ca,
            server,
            server_key,
            client,
            client_key,
        }
    }
    pub fn write(&self, directory: &std::path::Path) {
        use std::io::Write;
        for (name, data) in [
            ("ca.pem", self.ca.pem()),
            ("server.pem", self.server.pem()),
            ("server-key.pem", self.server_key.serialize_pem()),
            ("client.pem", self.client.pem()),
            ("client-key.pem", self.client_key.serialize_pem()),
        ] {
            let mut options = std::fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            options
                .open(directory.join(name))
                .unwrap()
                .write_all(data.as_bytes())
                .unwrap();
        }
    }
}
