"""Cross-language certificate regression invoked by the Rust mTLS tests."""
import json
from pathlib import Path
import socket
import ssl
import struct
import sys
import time
import uuid


def receive_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        chunk = stream.recv(count - len(data))
        if not chunk:
            raise EOFError("Rust daemon disconnected")
        data.extend(chunk)
    return bytes(data)


def receive(stream):
    count = struct.unpack(">I", receive_exact(stream, 4))[0]
    assert 0 < count <= 256 * 1024
    return json.loads(receive_exact(stream, count))


directory = Path(sys.argv[1])
context = ssl.create_default_context(cafile=str(directory / "ca.pem"))
context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
# Also enforce strict validation on Python versions predating the 3.13 default.
context.verify_flags |= ssl.VERIFY_X509_STRICT
context.load_cert_chain(directory / "client.pem", directory / "client-key.pem")
context.set_alpn_protocols(["jarvis-execution/1"])
with socket.create_connection(("127.0.0.1", int(sys.argv[2])), timeout=5) as raw:
    with context.wrap_socket(raw, server_hostname="localhost") as stream:
        assert stream.version() == "TLSv1.3"
        assert stream.selected_alpn_protocol() == "jarvis-execution/1"
        hello = receive(stream)
        assert hello["type"] == "hello" and hello["protocol"] == 1
        request_id = str(uuid.uuid4())
        request = {"protocol": 1, "session": hello["session"], "seq": 1,
                   "expires_at_ms": int(time.time() * 1000) + 5000,
                   "request_id": request_id, "capability_id": None, "action": {"kind": "status"}}
        encoded = json.dumps(request).encode("utf-8")
        stream.sendall(struct.pack(">I", len(encoded)) + encoded)
        result = receive(stream)
        assert result["type"] == "result" and result["request_id"] == request_id and result["ok"]
        assert result["data"]["simulation"] is True and result["data"]["native_input"] is False
        print("TLSv1.3 status verified")
