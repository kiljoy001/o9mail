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

For inbound mail, use 9front UPAS (`smtpd`, `send`, `upas/fs`) and a trusted
local maildrop adapter. The adapter must not trust arbitrary clients on the
AppUser session view as an SMTP delivery authority. Mail submission and mail
reading are distinct rights. Outgoing mail should use UPAS `marshal`/`send`
and factotum-managed server credentials where needed. SMTP/IMAP configuration
belongs in an encrypted per-user account view, with a worker managing each
user's polling schedule and errors.

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
