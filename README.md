# o9mail

An Objective-9C mail service for 9front. The current service provides AppUser
RSA authentication, encrypted mailboxes and account settings, and an importer
for UPAS IMAPS mailboxes. Outgoing mail and the graphical client are still
being built.

## Current service

`mailfs.o9` serves `/srv/<service>` with AppUser authentication. The operator
loads public RSA keys from a trusted local `.tab` file. After a user proves
ownership of the matching private key through stock 9front factotum, that
session receives its own view:

```text
/<session>/view/principal       verified AppUser name
/<session>/view/status          locked or unlocked
/<session>/view/unlock          write mailbox passphrase
/<session>/view/lock            write any text to forget the key
/<session>/view/inbox/index.tab opaque message identifiers
/<session>/view/inbox/select    write an identifier from index.tab
/<session>/view/inbox/message   selected raw RFC 5322 message
/<session>/view/inbox/origin    selected message's import source key
/<session>/view/inbox/append    staged raw message bytes
/<session>/view/inbox/source    optional stable import source key
/<session>/view/inbox/commit    write any text to encrypt staged bytes
/<session>/view/inbox/last      stored or duplicate, with message ID
/<session>/view/account/current.tab  configured IMAPS account
/<session>/view/account/{protocol,host,user,folder}  staged settings
/<session>/view/account/save   encrypt staged settings
```

Message bodies, headers, import source keys, and account settings are encrypted using the
Objective-9C `Vault` (Argon2id key derivation, XChaCha20-Poly1305). Each user
has a distinct random salt and a private 0700 directory. On disk, messages
are 0600 ciphertext files named by random identifiers. A check ciphertext
rejects a wrong passphrase before any message can be opened. `index.tab`
contains opaque IDs only; the service does not keep a plaintext subject index.
Messages are capped at 64 KiB and the visible index at 128 entries while the
Vault has whole-message encryption. A larger mail store needs chunked binary
encryption and paging before production use.

The current append interface accepts 9P writes at explicit offsets and commits
the staged message only when `inbox/commit` is written. This keeps split writes
from turning into separate messages. The staging buffer is memory-only and
bounded per session. Each authenticated session unlocks its own Vault; locking
one session clears its staged data and key without changing another session.
Imports use a source key inside the encrypted message envelope. Repeating a
sync selects the existing message instead of storing another copy. The first
storage format remains readable.

## IMAPS account and import

Unlock an authenticated view, then set all four account fields and save them:

```rc
view=/n/o9mail/0/view
echo imaps > $view/account/protocol
echo mail.example.com > $view/account/host
echo alice@example.com > $view/account/user
echo INBOX > $view/account/folder
echo save > $view/account/save
cat $view/account/current.tab
```

The account record contains no password. UPAS reads the matching
`proto=pass service=imap server=<host> user=<login>` key from the user's
factotum. Run `upas/fs` in that factotum namespace, with plumbing disabled,
then import through the authenticated view:

```rc
upas/fs -n -p -m /mail/fs
upas-sync sync $view /mail/fs mail.example.com
```

The final argument is the host you expect. `upas-sync` rejects a different
host in the account file. It requires a usable `/sys/lib/tls/mail` certificate
pin set before opening an IMAPS connection; 9front's UPAS TLS wrapper can
otherwise proceed without certificate verification. The optional fifth
argument names an already-open UPAS mailbox and imports from it without
starting a connection, for example a local test mailbox.

The importer reads at most the newest 128 UPAS message directories, skips
empty or oversized messages, and uses each message's UPAS digest plus the
account identity for encrypted deduplication. It does not delete remote mail.
The normal importer keeps a mailbox open in the supplied `upas/fs` instance.
Its name is derived from the host, login, and folder so later syncs can
refresh and reuse it without mixing accounts. Run a dedicated `upas/fs` for
this account and restart it after changing the account settings.
Sync is currently an explicit command; a scheduled worker and multiple
accounts per user remain to be built.

## Build

Requires Objective-9C main at or after commit `91ed8d6`, including
`serveAppUsers`, the binary-safe `Vault.open`, and the stock factotum RSA
provider. On 9front with
`o9build` installed and the Objective-9C stdlib bound into the source tree:

```rc
o9build mailfs.o9
o9build enroll.o9
o9build respond.o9
o9build upas-sync.o9
```

`mailfs` takes a service name, a private mailbox root, and a trusted enrollment
table. The private root must be owned by the service user and inaccessible to
group and other users. The enrollment table is generated by `enroll.o9`
for one principal, or by any trusted local tabula writer using schema
`o9mail_keys_v1` and columns `id,modulus,exponent`.

```rc
mailfs o9mail /mail/o9mail /lib/o9mail/keys.tab
```

An authenticated client allocates a session with `clone`, writes
`auth <principal>` to its `ctl`, signs `challenge.tab` through
`respond.o9`, and writes the result to `response.tab`. See
Objective-9C's `docs/APPUSER.md` for the full exchange.

The disposable VM check is:

```sh
O9_ROOT=/path/to/objective-9c \
  python3 test/test_mailfs_qemu.py /path/to/9front.img
```

Set `O9_ROOT` to a current Objective-9C checkout and `HALATHA_ROOT` if the
Halatha checkout is outside the sibling directory. This test starts a
disposable TLS IMAP server on the host and forwards the VM's IMAPS port to it
through QEMU. It checks two live network imports and a repeated sync, in
addition to the local UPAS fixture. The server implements the IMAP commands
used by UPAS for this test; no real mailbox credentials are used.

## Security boundary

AppUser authenticates a principal; it does not encrypt the 9P transport.
Only mount this service locally or over a separately protected channel.
Writing a passphrase to `unlock` over cleartext 9P would expose it. Keep the
enrollment table under operator control; public self-enrollment would defeat
the gate. The service does not call `Vault.openTab`, because that helper
currently writes a temporary plaintext `.tab` file.

After a daemon restart, every mailbox is locked. The importer runs only with
an unlocked authenticated view and cannot pull unattended after a restart.
There is no server escrow. See [docs/DESIGN.md](docs/DESIGN.md) for the
delivery design and limits.
