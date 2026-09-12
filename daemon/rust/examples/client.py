"""Small real mTLS IPC client; Python standard library only. No desktop mutation.

python examples/client.py local-pki status
python examples/client.py local-pki capture
python examples/client.py local-pki emergency_stop
"""
import json
from pathlib import Path
import socket
import ssl
import struct
import sys
import time
import uuid

MAX_FRAME = 256 * 1024


def receive_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        chunk = stream.recv(count - len(data))
        if not chunk:
            raise EOFError("daemon closed connection")
        data.extend(chunk)
    return bytes(data)


def receive(stream):
    count = struct.unpack(">I", receive_exact(stream, 4))[0]
    if not 0 < count <= MAX_FRAME:
        raise ValueError("invalid frame length")
    return json.loads(receive_exact(stream, count))


def main():
    directory = Path(sys.argv[1])
    command = sys.argv[2] if len(sys.argv) > 2 else "status"
    if command not in {"status", "capture", "emergency_stop"}:
        raise ValueError("use status, capture, or emergency_stop")
    context = ssl.create_default_context(cafile=str(directory / "ca.pem"))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(directory / "client.pem", directory / "client-key.pem")
    context.set_alpn_protocols(["jarvis-execution/1"])
    with socket.create_connection(("127.0.0.1", 7443), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname="localhost") as stream:
            hello = receive(stream)
            if hello.get("protocol") != 1 or hello.get("type") != "hello":
                raise ValueError("unsupported daemon")
            action = {"kind": command}
            if command == "capture":
                action["display_id"] = 0
            request = {
                "protocol": 1, "session": hello["session"], "seq": 1,
                "expires_at_ms": int(time.time() * 1000) + 5000,
                "request_id": str(uuid.uuid4()),
                "capability_id": "dev-observe" if command == "capture" else None,
                "action": action,
            }
            encoded = json.dumps(request, separators=(",", ":")).encode()
            stream.sendall(struct.pack(">I", len(encoded)) + encoded)
            while True:
                reply = receive(stream)
                print(json.dumps(reply, ensure_ascii=True))
                if reply.get("type") == "result" and reply.get("request_id") == request["request_id"]:
                    return 0 if reply["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
