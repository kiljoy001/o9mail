"""Disposable TLS IMAP fixture for the 9front end-to-end test.

Only the commands issued by upas/fs are implemented. The fixture verifies a
real TCP/TLS/factotum/UPAS path; it is not a general-purpose IMAP server.
"""

import hashlib
import re
import socket
import ssl
import subprocess
import threading
from pathlib import Path


HOST = "10.0.2.100"
LOGIN = "alice@local.test"
PASSWORD = "test-imaps-only-68142"
MESSAGES = (
    b"From: sender@local.test\r\nTo: alice@local.test\r\n"
    b"Subject: live-imaps-finch-7043\r\nDate: Wed, 14 Oct 2026 10:00:00 +0000\r\n\r\n"
    b"First live message.\r\n",
    b"From: sender@local.test\r\nTo: alice@local.test\r\n"
    b"Subject: live-imaps-heron-8261\r\nDate: Wed, 14 Oct 2026 10:01:00 +0000\r\n\r\n"
    b"Second live message.\r\n",
)


class LiveImaps:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        key = directory / "imap.key"
        cert = directory / "imap.crt"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(cert), "-days", "1",
             "-subj", "/CN=live-imaps.local"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        der = ssl.PEM_cert_to_DER_cert(cert.read_text())
        self.trust = directory / "mail"
        self.trust.write_text("x509 sha256=" + hashlib.sha256(der).hexdigest() + "\n")
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.maximum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(cert, key)
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.5)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.errors = []
        self.commands = []
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(3)

    def _serve(self):
        while not self.stop.is_set():
            try:
                raw, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(raw,), daemon=True).start()

    def _handle(self, raw):
        try:
            with self.context.wrap_socket(raw, server_side=True) as conn:
                conn.settimeout(20)
                self._session(conn)
        except (OSError, ssl.SSLError) as error:
            if not self.stop.is_set():
                self.errors.append(str(error))
            raw.close()

    def _session(self, conn):
        conn.sendall(b"* OK live-imaps fixture ready\r\n")
        reader = conn.makefile("rb")
        authenticated = False
        selected = False
        while not self.stop.is_set():
            line = reader.readline(4096)
            if not line:
                return
            match = re.fullmatch(rb"([^ ]+) ([A-Za-z]+)(?: (.*))?\r?\n", line)
            if not match:
                return
            tag, verb, args = match.groups()
            verb = verb.lower()
            args = (args or b"").rstrip(b"\r")
            self.commands.append(verb.decode())
            if verb == b"capability":
                conn.sendall(b"* CAPABILITY IMAP4rev1\r\n")
            elif verb == b"login":
                parts = args.replace(b'"', b"").split(b" ", 1)
                authenticated = len(parts) == 2 and parts[0] == LOGIN.encode() and parts[1] == PASSWORD.encode()
                if not authenticated:
                    conn.sendall(tag + b" NO invalid login\r\n")
                    continue
            elif verb == b"select" and authenticated:
                selected = args.strip(b'"').upper() == b"INBOX"
                if selected:
                    conn.sendall(b"* 2 EXISTS\r\n* OK [UIDVALIDITY 12345] ready\r\n")
            elif verb == b"status" and selected:
                conn.sendall(b"* STATUS INBOX (MESSAGES 2 UIDVALIDITY 12345)\r\n")
            elif verb == b"fetch" and selected:
                for i, raw in enumerate(MESSAGES, 1):
                    conn.sendall(
                        f'* {i} FETCH (UID {100+i} FLAGS () RFC822.SIZE {len(raw)} '
                        'INTERNALDATE "14-Oct-2026 10:00:00 +0000")\r\n'.encode()
                    )
            elif verb == b"uid" and selected:
                fetch = re.fullmatch(rb"fetch (\d+) \(flags body\.peek\[\]<([0-9]+)\.([0-9]+)>\)",
                                     args, re.IGNORECASE)
                if not fetch:
                    conn.sendall(tag + b" BAD unsupported UID command\r\n")
                    continue
                uid, offset, count = (int(part) for part in fetch.groups())
                index = uid - 100
                if index not in (1, 2):
                    conn.sendall(tag + b" NO unknown UID\r\n")
                    continue
                fragment = MESSAGES[index-1][offset:offset+count]
                conn.sendall(
                    f"* {index} FETCH (UID {uid} FLAGS () BODY[]<{offset}> "
                    f"{{{len(fragment)}}}\r\n".encode() + fragment + b")\r\n"
                )
            elif verb == b"noop":
                pass
            elif verb == b"logout":
                conn.sendall(b"* BYE closing\r\n")
                conn.sendall(tag + b" OK logout\r\n")
                return
            else:
                conn.sendall(tag + b" BAD unsupported command\r\n")
                continue
            conn.sendall(tag + b" OK done\r\n")
