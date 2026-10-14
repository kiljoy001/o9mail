# Mail service design

The service is an application server mounted through 9P. AppUser's verified
principal chooses the mailbox; client path names, Plan 9 usernames, display
names, and email addresses never choose one. The private key stays in the
user's factotum. The service's local enrollment table is the authorization
source until an operator adds a managed enrollment flow.

```text
9front factotum ──sign challenge──> AppUser gate
                                     │ verified principal
                                     v
                               private 9P view
                                     │
                             unlocked Vault in RAM
                                     │
                         ciphertext mailbox on disk
```

An unlock passphrase derives a symmetric per-user Vault key from a stored
random salt. The first unlock writes an encrypted check value. A later unlock
must open that check value. The service only stores opaque, authenticated
ciphertext. This suits user-controlled storage but means unattended fetching
cannot resume after a daemon restart. The delivery worker must queue securely
or return a temporary failure until unlock; an eventual device-held recipient
key or public-key envelope could remove that availability limit.

For inbound IMAPS mail, `upas-sync` uses a configured account from the
authenticated view, asks an already-running `upas/fs` to open that account,
and imports raw messages. UPAS uses the caller's factotum for the IMAP
credential. The helper requires a mail certificate pin set before it opens a
network connection. Only an `imaps` account is accepted. The configured
hostname must match a separate command-line expected hostname. The account
record is encrypted at rest and does not carry the password.

Each import source key lives inside the authenticated ciphertext along with
the raw message. This makes a message and its dedup identity one durable
unit. Checking for a prior key decrypts up to 128 bounded messages; indexing
and paging are future work. A digest collision or two identical deliveries
in the same account can coalesce, so a later version should use IMAP UID and
UIDVALIDITY as the primary source key.

For SMTP delivery, use 9front UPAS (`smtpd`, `send`) and a trusted local
maildrop adapter. That adapter must not treat arbitrary AppUser clients as
SMTP delivery authorities. Mail submission and mail reading are distinct
rights. Outgoing mail should use UPAS `marshal`/`send` and factotum-managed
server credentials where needed. A scheduled IMAPS worker still needs
per-user polling, backoff, and a way to handle locked mailboxes.

The GUI will follow the Evolution pattern: account list, folder tree, message
list, message view, composer, and a setup dialog. It will consume only the
mounted service files. The wire data is `.tab` where structured data fits;
raw RFC 5322 bytes remain raw. A future index can expose subject, sender,
date, flags, and threading after unlock, generated from encrypted metadata.

Before production, add chunked binary AEAD with bounded memory, durable
atomic writes, message paging, deletion and quota controls, per-user rate
limits, session expiry and cleanup, protected transport, and a test of
multiuser isolation and restart recovery on 9front. Today `mailfs` is an
authenticated encrypted mailbox prototype, not an SMTP server.
