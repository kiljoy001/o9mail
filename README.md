# o9mail: Native Zero-Knowledge Email System for 9front

`o9mail` is a complete, sovereign, zero-knowledge email transfer agent (MTA), mailbox filesystem, and cryptographic security stack written in **Objective-9c (`o9`)** for **Plan 9 / 9front**.

It enables standalone 9front nodes to participate directly in the modern federated email internet—communicating securely with providers like Gmail, Yahoo, Apple, and Outlook—**without** intermediary Linux relays, OpenSSL, Python, or Milter dependencies.

---

## Architectural Highlights

```text
       [ Modern Internet / External MTAs ]
                        │
                        ▼ (SMTP Port 2525)
              ┌───────────────────┐
              │     smtpd.o9      │  ◄── Remote IP from /net/tcp/<conv>/remote
              └─────────┬─────────┘
                        │
  ┌─────────────────────┼─────────────────────┐
  ▼                     ▼                     ▼
[ SPF RFC 7208 ]   [ DKIM RFC 6376 ]   [ DMARC RFC 7489 ]
  via /net/dns       RSA-SHA256          Identifier Alignment
  mechanisms:        relaxed/relaxed     Enforcement:
  ip4, ip6, mx,      body canonical.     p=reject (550 5.7.1)
  include, ~all      b=, bh= tags        p=quarantine, none
  └─────────────────────┬─────────────────────┘
                        │
                        ▼
         [ Prepend RFC 8601 Authentication-Results ]
                        │
                        ▼
       [ Zero-Knowledge Vault Hybrid Sealing ]
         Ephemeral Message Key (Monocypher XChaCha20-Poly1305)
         Sealed with Recipient RSA-2048 Public Key (libsec PKCS #1 v1.5)
         Container: O9SEAL1\n<sealed_key_hex>\n<ciphertext_hex>
                        │
                        ▼
      [ Stored on Disk: /usr/scott/mail/o9mail/<user>/<mid>.enc ]
      (Server CANNOT read plaintext after writing; 0600 mode)

──────────────────────────────────────────────────────────────────

       [ Plan 9 User / Rio / Acme Session ]
                        │
                        ▼ 9P mount /srv/mailfs /tmp/mailmnt
              ┌───────────────────┐
              │     mailfs.o9     │  ◄── Factotum Auth (/mnt/factotum/rpc)
              └─────────┬─────────┘
                        │
  ┌─────────────────────┴─────────────────────┐
  ▼ (Reading Mail)                            ▼ (Sending Mail)
[ Unseal O9SEAL1 via Factotum ]             [ Stage outbox/{to,subject,body} ]
  Recipient RSA private key                   Write 'send' to outbox/send
  decrypts ephemeral message key              Auto-discover domain DKIM key
  Decodes O9MAIL2 envelope                    Sign with relaxed/relaxed RSA-SHA256
  Auto-reseals to user symmetric Vault        Dispatch to smtpout / remote MX
                                              Archive sealed copy to Sent vault
```

---

## Core Components

### 1. `smtpd.o9` (Inbound SMTP Daemon)
* **RFC 5321 Engine**: Multi-session concurrency over Plan 9 `/net/tcp` with clean state transitions (`HELO`/`EHLO`, `MAIL FROM`, `RCPT TO`, `DATA`, `QUIT`, `RSET`).
* **Connection Introspection**: Queries `/net/tcp/<conv>/remote` to determine the remote client IP address dynamically.
* **Security Evaluation**:
  * Evaluates **SPF (RFC 7208)** upon `MAIL FROM:`.
  * Verifies **DKIM (RFC 6376)** signatures upon message completion in `DATA`.
  * Evaluates **DMARC (RFC 7489)** policy against the `From:` header domain.
  * Enforces `p=reject` policies immediately at the SMTP wire level (`550 5.7.1 DMARC policy violation for <domain>`), stopping spoofed emails before they reach storage.
* **RFC 8601 Stamping**: Prepends an authenticated provenance header recording DKIM, SPF, and DMARC results to all accepted mail.
* **Zero-Knowledge Ingress (`O9SEAL1`)**: Generates an ephemeral 256-bit symmetric key, encrypts the message with **Monocypher XChaCha20-Poly1305**, seals the ephemeral key with the recipient's enrolled RSA-2048 public key via **`libsec` PKCS #1 v1.5**, and persists to disk. Once sealed, the server process cannot read the plaintext back.

### 2. `security.o9` (Cryptographic Security Suite)
* **DKIM (RFC 6376)**:
  * Relaxed/relaxed header and body canonicalization with whitespace reduction and CRLF normalization.
  * Computes body hash `bh=` with SHA-256.
  * Signs headers with RSA-2048 (`rsasign` using `sha256_prefix` digest info).
  * Inbound signature verification parsing `v=`, `a=`, `d=`, `s=`, `c=`, `h=`, `bh=`, `b=` tags against `/net/dns` public keys.
* **SPF (RFC 7208)**:
  * Queries `/net/dns` for `<domain> txt` records.
  * Parses IPv4 client addresses and tests CIDR masks against `ip4:`, `ip6:`, `a`, `mx`, `include:`, and `~all`/`-all` mechanisms.
  * Loopback IP bypass strictly applies only to local domains (`localhost` / `/dev/sysname` / `/net/ndb` `dom=`).
* **DMARC (RFC 7489)**:
  * Queries `/net/dns` for `_dmarc.<fromDomain> txt` records.
  * Checks identifier alignment for both SPF and DKIM against the RFC 5322 `From:` domain.
  * Enforces `p=reject`, `p=quarantine`, or `p=none`.
* **Authentication-Results (RFC 8601)**:
  * Produces standardized authentication headers recording all provenance verdicts.
* **Dynamic Local Domain (`SmtpLocalDomainRaw`)**:
  * Dynamically inspects `/net/ndb` for `dom=` or falls back to `/dev/sysname`, completely avoiding hardcoded domain strings.

### 3. `mailfs.o9` (9P Mailbox Filesystem)
* **AppUser RSA Authentication**: Validates sessions via challenge-response using Plan 9's native `factotum` (`proto=rsa service=appuser app=o9mail role=client`).
* **Zero-Knowledge Decryption**: Unseals `O9SEAL1` envelopes on-the-fly using the user's Factotum private key via `/mnt/factotum/rpc` and auto-reseals them into the user's unlocked symmetric Vault for instantaneous future access.
* **Synthetic 9P Tree**:
  ```text
  /<session>/view/inbox/index.tab    opaque message identifiers
  /<session>/view/inbox/select       select message by identifier
  /<session>/view/inbox/message      selected raw RFC 5322 message
  /<session>/view/inbox/origin       selected message's origin key
  /<session>/view/outbox/to          staged recipient address
  /<session>/view/outbox/subject     staged subject
  /<session>/view/outbox/body        staged body text
  /<session>/view/outbox/send        write 'send' to sign and dispatch
  /<session>/view/outbox/status      status of last send ('sent <mid>' or error)
  ```
* **Outbox DKIM Auto-Signing**: Automatically detects domain DKIM keys in `/mail/o9mail/` or `/tmp/`, signs queued mail before transmission, and saves a sealed copy into the sender's encrypted Sent vault.

### 4. `smtpout.o9` (Outbound Delivery Agent)
* **RFC 5321 Client**: Direct delivery to remote destination MX records or smarthosts.
* **DNS MX Resolution**: Resolves MX records via native `/net/dns` lookups.
* **Outbound DKIM Signing**: Signs outbound mail using specified `-k <keyfile>` or auto-discovered domain keys with `-sel <selector>`.

### 5. `o9dkim.o9` (DKIM Keypair Generator)
* Generates 2048-bit RSA keypairs using `rsagen(2048, 18, 0)` ($e=65537$).
* Writes factotum-compatible private key files.
* Outputs ready-to-publish DNS TXT records for DKIM (`<selector>._domainkey.<domain>`), SPF, and DMARC.

### 6. `enroll.o9` (Recipient Enrollment)
* Enrolls trusted recipient AppUser RSA public keys into the Tabula schema (`trusted_keys.tab`).

### 7. `upas-sync.o9` (UPAS IMAPS Mailbox Importer)
* Imports existing mailboxes from remote IMAPS servers using 9front's `upas/fs`.
* Credentials are read directly from the user's Factotum (`proto=pass service=imap server=<host> user=<login>`).
* Validates TLS certificate pins against `/sys/lib/tls/mail`.
* Performs encrypted deduplication using message digests, packaging imported mail directly into the user's zero-knowledge vault.

### 8. `o9admin.o9` (Native GUI Server Administration Console)
* **Zero Command-Line Dependency**: Full graphical administration panel built natively in `o9` using Plan 9 `/dev/draw` and `libdraw`. No web servers, Python, or REST sidecars.
* **Designing Interfaces (O'Reilly) Architecture**:
  * **Card Stack Navigation (`DrawTabBar`)**: 4 primary workspaces (`Services & Engine`, `DKIM & DNS Setup`, `AppUsers & Vaults`, `Ingress Audit Log`).
  * **Status Annunciators (`DrawBadge` & `DrawCard`)**: Color-coded real-time service indicators (`RUNNING`, `STOPPED`, `MOUNTED`, `ENFORCING`).
  * **Master-Detail View (`DrawSplitPane` & `DrawTable`)**: Dual-pane directory inspector for enrolled recipients and vault properties.
  * **Property Sheet Inspector (`DrawPropertySheet`)**: Formatted DNS TXT directive display (DKIM, SPF, DMARC) ready to copy to nameservers.
  * **Direct Action Controls (`DrawButton`, `DrawTextInput`)**: One-click keypair generation, service start/stop, and recipient enrollment.
  * **Modal Safety Gate (`DrawDialog`)**: Confirms dangerous operations (e.g. stopping `smtpd`, revoking keys) before execution.
  * **Global Status Anchor (`DrawStatusBar`)**: Bottom status footer displaying active host node, port state, and zero-knowledge integrity notices.

---

## 9P View and Factotum Protocol Specification

When a client attaches to `/srv/<service>` (e.g. `/srv/mailfs`), the connection enters an unauthenticated session gate.

### 1. Challenge-Response Handshake
```rc
mount /srv/mailfs /mail
sid=`{cat /mail/clone}
echo auth <principal> > /mail/$sid/ctl
```
The server emits `challenge.tab` containing an ephemeral nonce tied to this session. The user proves ownership of the matching RSA private key via `factotum` (`/mnt/factotum/rpc` with `service=appuser app=o9mail role=client`):
```rc
respond respond <principal> $n $ek /mail/$sid/challenge.tab /tmp/response.tab
cat /tmp/response.tab > /mail/$sid/response.tab
```
Upon successful verification, the session's private view tree is unlocked.

### 2. Private 9P Session Tree (`/<session>/view/`)
```text
/<session>/view/principal          verified AppUser principal name
/<session>/view/status             mailbox state ('locked' or 'unlocked')
/<session>/view/unlock             write passphrase to unlock symmetric Vault
/<session>/view/lock               write any text to clear key and lock
/<session>/view/inbox/index.tab    opaque message identifiers
/<session>/view/inbox/select       write identifier from index.tab to select
/<session>/view/inbox/message      selected raw RFC 5322 message
/<session>/view/inbox/origin       selected message's origin key (e.g. smtp:sender:mid)
/<session>/view/inbox/append       staged raw message bytes for local injection
/<session>/view/inbox/source       optional stable import source key
/<session>/view/inbox/commit       write any text to encrypt and store staged bytes
/<session>/view/inbox/last         status of last committed message
/<session>/view/outbox/to          staged recipient address
/<session>/view/outbox/subject     staged message subject
/<session>/view/outbox/body        staged message body
/<session>/view/outbox/send        write 'send' to DKIM-sign, dispatch, and archive
/<session>/view/outbox/status      status of last send ('sent <mid>' or 'error: ...')
/<session>/view/account/current.tab  configured remote IMAPS account
/<session>/view/account/{protocol,host,user,folder}  staged IMAPS settings
/<session>/view/account/save       save and encrypt staged account settings
```

---

## Security Boundary and Threat Model

* **Transport Layer**: AppUser authenticates the client principal to the server; it does not encrypt the 9P wire transport. Only mount `/srv/mailfs` locally or over a secured network channel (such as TLS or a trusted grid network).
* **Zero-Knowledge Ingress**: `smtpd` uses public-key hybrid encryption (`O9SEAL1`). When an inbound email arrives, it is sealed using the recipient's RSA public key. The daemon has no access to recipient private keys and **cannot read** stored messages after writing them to disk.
* **At-Rest Vault Encryption**: Mailboxes and account records on disk are encrypted using Monocypher XChaCha20-Poly1305 and Argon2id key derivation. Each user directory (`0700`) contains random opaque `.enc` files (`0600`).
* **Daemon Restart State**: After a daemon restart, all mailboxes remain locked. Unsealing requires the authenticated client presenting their Factotum credentials. There is no server escrow of user private keys.
* **Enrollment Gate**: The operator controls `trusted_keys.tab`. Arbitrary self-enrollment is rejected to prevent mailbox impersonation.

---

## Building and Compiling

Requires `o9c` (Objective-9c compiler) on Linux or Plan 9.

### Step 1: Compile `.o9` to C
```bash
/path/to/o9c -I/path/to/o9mail smtpd.o9 > smtpd.c
/path/to/o9c -I/path/to/o9mail smtpout.o9 > smtpout.c
/path/to/o9c -I/path/to/o9mail mailfs.o9 > mailfs.c
/path/to/o9c -I/path/to/o9mail o9dkim.o9 > o9dkim.c
/path/to/o9c -I/path/to/o9mail o9admin.o9 > o9admin.c
```

### Step 2: Build on 9front
On 9front (with native Plan 9 libraries):
```rc
6c -FV -I/usr/scott/include -o smtpd.6 smtpd.c
6l -o smtpd smtpd.6 /usr/scott/lib/o9/libo9.a /amd64/lib/libndb.a

6c -FV -I/usr/scott/include -o smtpout.6 smtpout.c
6l -o smtpout smtpout.6 /usr/scott/lib/o9/libo9.a /amd64/lib/libndb.a

6c -FV -I/usr/scott/include -o mailfs.6 mailfs.c
6l -o mailfs mailfs.6 /usr/scott/lib/o9/libo9.a /amd64/lib/libndb.a

6c -FV -I/usr/scott/include -o o9dkim.6 o9dkim.c
6l -o o9dkim o9dkim.6 /usr/scott/lib/o9/libo9.a /amd64/lib/libndb.a

6c -FV -I/usr/scott/include -o o9admin.6 o9admin.c
6l -o /usr/scott/bin/amd64/o9admin o9admin.6 /usr/scott/lib/o9/libo9.a /amd64/lib/libdraw.a /amd64/lib/libmemdraw.a /amd64/lib/libmemlayer.a /amd64/lib/libsec.a /amd64/lib/libmp.a /amd64/lib/libndb.a
```

---

## Quickstart & Operational Guide

### 0. Graphical Administration (No Command-Line Required)
Launch the graphical console inside Rio or drawterm:
```rc
o9admin -i
```
Or run the built-in self-verification test suite headlessly:
```rc
o9admin
```
The administration panel provides complete control over:
1. Starting and stopping `smtpd` (inbound engine) and `mailfs` (9P mailbox service).
2. Generating 2048-bit RSA DKIM keys and inspecting formatted DNS TXT directives.
3. Managing AppUser recipients and inspecting enrolled public modulus keys.
4. Monitoring live incoming SMTP handshakes, DMARC wire rejections, and zero-knowledge encryption events.

### 1. Generate DKIM Keypair and DNS Records
```rc
o9dkim genkey mydomain.org 2026 /mail/o9mail/dkim_mydomain.org.key
```
This generates the private key and displays the exact DNS records to publish:
* **DKIM**: `2026._domainkey.mydomain.org IN TXT "v=DKIM1; k=rsa; p=..."`
* **SPF**: `mydomain.org IN TXT "v=spf1 mx a -all"`
* **DMARC**: `_dmarc.mydomain.org IN TXT "v=DMARC1; p=reject; rua=mailto:postmaster@mydomain.org"`

### 2. Enroll Recipients
Create the trusted keys table and enroll authorized mailbox recipients:
```rc
cat <<! > /mail/o9mail/trusted_keys.tab
schema=o9mail_keys_v1
	col=id
	col=modulus
	col=exponent

id=alice
	modulus=FFD5E35075...
	exponent=10001
!
```

### 3. Start the Inbound SMTP Daemon
```rc
smtpd tcp!*!25 mydomain.org /mail/o9mail/trusted_keys.tab /usr/scott/mail/o9mail /tmp/o9spool &
```

### 4. Start the 9P Mailbox Service
```rc
mailfs mailfs /usr/scott/mail/o9mail /mail/o9mail/trusted_keys.tab 127.0.0.1 25 mydomain.org &
```

### 5. Read and Send Email via 9P
Mount the mailbox filesystem inside your authenticated session:
```rc
# Load private key into factotum
cat /usr/scott/keyring/alice.key > /mnt/factotum/ctl

# Mount synthetic filesystem
mount /srv/mailfs /mail

# Unlock your private symmetric vault
sid=`{cat /mail/clone}
# ... perform challenge-response authentication with /mail/$sid/ctl ...
echo 'my-passphrase' > /mail/$sid/view/unlock

# Read inbound mail
cat /mail/$sid/view/inbox/index.tab
echo '<mid>' > /mail/$sid/view/inbox/select
cat /mail/$sid/view/inbox/message

# Send signed mail
echo -n 'recipient@remote.com' > /mail/$sid/view/outbox/to
echo -n 'Subject line' > /mail/$sid/view/outbox/subject
echo 'Hello from 9front!' > /mail/$sid/view/outbox/body
echo send > /mail/$sid/view/outbox/send
cat /mail/$sid/view/outbox/status
```

---

## Verification & Testing

A complete automated unit and integration suite is included:
* **`test_sec.o9`**: Verifies DKIM RSA signing, verification, relaxed canonicalization, SPF resolution, and DMARC alignment.
* **`test_mailfs_dkim.rc`**: Full end-to-end integration test validating Factotum challenge-response authentication, 9P outbox staging, DKIM auto-signing, SMTP receipt, DMARC policy check, and zero-knowledge vault sealing.

---

## License

BSD 3-Clause. Designed and built for Plan 9 and 9front.
