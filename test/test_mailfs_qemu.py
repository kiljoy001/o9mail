#!/usr/bin/env python3
"""Exercise AppUser mailboxes and a local UPAS import in a 9front VM."""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from live_imaps import HOST as IMAP_HOST, LOGIN as IMAP_LOGIN, PASSWORD as IMAP_PASSWORD, LiveImaps


PROJECT = Path(__file__).resolve().parents[1]
O9 = Path(os.environ.get("O9_ROOT", PROJECT.parent / "objective-9c")).resolve()
HALATHA = Path(os.environ.get("HALATHA_ROOT", PROJECT.parent / "halatha")).resolve()
BUILDER = HALATHA / "extras/9front/kernel/build-kernel.py"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: test_mailfs_qemu.py /path/to/9front.img")
    spec = importlib.util.spec_from_file_location("builder", BUILDER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    with tempfile.TemporaryDirectory(prefix="o9mail-qemu-") as temporary:
        work = Path(temporary)
        imaps = LiveImaps(work / "imaps")
        for name in ("mailfs", "enroll", "respond", "upas-sync"):
            with (work / f"{name}.c").open("w") as output:
                subprocess.run([str(O9 / "o9c/o9c"), f"{name}.o9"],
                               cwd=PROJECT, stdout=output, check=True)
        with ((O9 / "o9c/test/e2e_vault.o9").open("rb") as source,
              (work / "vault-test.c").open("wb") as output):
            subprocess.run([str(O9 / "o9c/o9c")], cwd=O9, stdin=source,
                           stdout=output, check=True)
        disk = work / "test.img"
        subprocess.run(["cp", "--sparse=always", sys.argv[1], disk], check=True)
        fat = f"{disk}@@{builder.ninefat_offset(disk)}"
        (work / "plan9.ini").write_text(builder.BUILD_PLAN9INI)
        subprocess.run(["mcopy", "-o", "-i", fat, str(work / "plan9.ini"),
                        "::plan9.ini"], check=True)
        subprocess.run(["mmd", "-i", fat, "::o9mail"], check=True)
        subprocess.run(["mmd", "-i", fat, "::o9mlib"], check=True)
        runtime = ("mkfile", "o9.h", "o9_runtime.c", "o9_filetree.c",
                   "o9_view.c", "o9_tab_discard.c", "o9_crypto.c",
                   "monocypher.c", "monocypher.h", "o9_dispatch_386.s")
        for name, path in ((name, work / name) for name in
                           ("mailfs.c", "enroll.c", "respond.c", "upas-sync.c", "vault-test.c")):
            subprocess.run(["mcopy", "-i", fat, str(path),
                            f"::o9mail/{name}"], check=True)
        for name in runtime:
            subprocess.run(["mcopy", "-i", fat, str(O9 / name),
                            f"::o9mail/{name}"], check=True)
        for path in (O9 / "libtab").glob("*.[ch]"):
            subprocess.run(["mcopy", "-i", fat, str(path),
                            f"::o9mlib/{path.name}"], check=True)
        subprocess.run(["mcopy", "-i", fat, str(imaps.trust),
                        "::o9mail/mail-trust"], check=True)

        qemu = ["qemu-system-i386", "-m", "1024", "-display", "none",
                "-monitor", "none", "-serial", "stdio", "-no-reboot",
                "-netdev", f"user,id=n0,guestfwd=tcp:{IMAP_HOST}:993-tcp:127.0.0.1:{imaps.port}",
                "-device", "ne2k_pci,netdev=n0",
                "-drive", f"file={disk},format=raw,if=ide,index=0"]
        if os.access("/dev/kvm", os.R_OK | os.W_OK):
            qemu.insert(1, "-enable-kvm")
        logpath = work / "serial.log"
        with logpath.open("w") as log:
            proc = subprocess.Popen(qemu, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            imaps.start()
            console = builder.Console(proc, log)
            serial = 0

            def guest(command, timeout=90):
                nonlocal serial
                marker = f"MAILEND{serial:04d}"
                serial += 1
                proc.stdin.write((command + "; echo " + marker + "\n").encode())
                proc.stdin.flush()
                if console.expect([marker + "\r\nterm% ", marker + "\nterm% "], timeout) is None:
                    raise RuntimeError(f"guest command timed out: {command}")

            try:
                if console.expect(["term% "], 180) is None:
                    raise RuntimeError("9front prompt unavailable")
                for command in (
                    "9fs 9fat", "ramfs", "mkdir /tmp/o9mail", "mkdir /tmp/o9mail/libtab",
                    "cp /n/9fat/o9mail/* /tmp/o9mail",
                    "cp /n/9fat/o9mlib/* /tmp/o9mail/libtab",
                    "bind -b /usr/glenda/bin/386 /bin",
                    "cd /tmp/o9mail; mk libo9.a",
                    "8c -FV -DO9_HAVE_SRVRELEASE -I/tmp/o9mail -o /tmp/o9mail/mailfs.8 /tmp/o9mail/mailfs.c",
                    "8c -FV -DO9_HAVE_SRVRELEASE -I/tmp/o9mail -o /tmp/o9mail/enroll.8 /tmp/o9mail/enroll.c",
                    "8c -FV -DO9_HAVE_SRVRELEASE -I/tmp/o9mail -o /tmp/o9mail/respond.8 /tmp/o9mail/respond.c",
                    "8c -FV -DO9_HAVE_SRVRELEASE -I/tmp/o9mail -o /tmp/o9mail/upas-sync.8 /tmp/o9mail/upas-sync.c",
                    "8c -FV -DO9_HAVE_SRVRELEASE -I/tmp/o9mail -o /tmp/o9mail/vault-test.8 /tmp/o9mail/vault-test.c",
                    "8l -o /tmp/mailfs /tmp/o9mail/mailfs.8 /tmp/o9mail/libo9.a /386/lib/libndb.a /386/lib/libauth.a /386/lib/libmp.a /386/lib/libsec.a",
                    "8l -o /tmp/mail-enroll /tmp/o9mail/enroll.8 /tmp/o9mail/libo9.a /386/lib/libndb.a /386/lib/libauth.a /386/lib/libmp.a /386/lib/libsec.a",
                    "8l -o /tmp/mail-respond /tmp/o9mail/respond.8 /tmp/o9mail/libo9.a /386/lib/libndb.a /386/lib/libauth.a /386/lib/libmp.a /386/lib/libsec.a",
                    "8l -o /tmp/upas-sync /tmp/o9mail/upas-sync.8 /tmp/o9mail/libo9.a /386/lib/libndb.a /386/lib/libauth.a /386/lib/libmp.a /386/lib/libsec.a",
                    "8l -o /tmp/vault-test /tmp/o9mail/vault-test.8 /tmp/o9mail/libo9.a /386/lib/libndb.a /386/lib/libauth.a /386/lib/libmp.a /386/lib/libsec.a",
                    "/tmp/vault-test",
                    "mkdir /tmp/o9mail-private", "chmod 700 /tmp/o9mail-private",
                    "auth/rsagen -b 2048 -t 'service=appuser app=o9mail hash=sha256' >/tmp/o9mail-key",
                    "cat /tmp/o9mail-key >/mnt/factotum/ctl",
                    "n=`{awk '{for(i=1;i<=NF;i++)if($i~/^n=/){sub(/^n=/,\"\",$i);print $i}}' /tmp/o9mail-key}",
                    "e=`{awk '{for(i=1;i<=NF;i++)if($i~/^ek=/){sub(/^ek=/,\"\",$i);print $i}}' /tmp/o9mail-key}",
                    "/tmp/mail-enroll enroll /tmp/o9mail-keys.tab alice $n $e",
                    "/tmp/mailfs mailtest /tmp/o9mail-private /tmp/o9mail-keys.tab </dev/null >/tmp/mailfs.log >[2=1] &",
                    "sleep 2", "chmod 666 /srv/mailtest", "mkdir /tmp/mailmnt",
                    "mount /srv/mailtest /tmp/mailmnt",
                    "cat /tmp/mailmnt/clone", "mkdir /tmp/othermnt",
                    "mount /srv/mailtest /tmp/othermnt",
                    "cat /tmp/othermnt/0/view/principal >[2=1]",
                    "echo auth alice >/tmp/mailmnt/0/ctl",
                    "/tmp/mail-respond respond alice $n $e /tmp/mailmnt/0/challenge.tab /tmp/o9mail-response.tab",
                    "cat /tmp/o9mail-response.tab >/tmp/mailmnt/0/response.tab",
                    "cat /tmp/mailmnt/0/status.tab",
                    "cat /tmp/mailmnt/0/view/principal",
                    "echo correct-passphrase-123 >/tmp/mailmnt/0/view/unlock",
                    "cat /tmp/mailmnt/0/view/status",
                    "echo 'Subject: secret-owl-5937' >/tmp/mailmnt/0/view/inbox/append",
                    "echo save >/tmp/mailmnt/0/view/inbox/commit",
                    "cat /tmp/mailmnt/0/view/inbox/index.tab >/tmp/mail-index.tab",
                    "cat /tmp/mail-index.tab",
                    "ls /tmp/o9mail-private/alice",
                    "grep secret-owl-5937 /tmp/o9mail-private/alice/*.enc >/tmp/mail-leak",
                    "wc -c /tmp/mail-leak",
                    "mid=`{awk '/^id=/{sub(/^id=/,\"\");print}' /tmp/mail-index.tab}",
                    "echo $mid >/tmp/mailmnt/0/view/inbox/select",
                    "cat /tmp/mailmnt/0/view/inbox/message",
                    "echo lock >/tmp/mailmnt/0/view/lock",
                    "cat /tmp/mailmnt/0/view/status",
                    "cat /tmp/mailmnt/0/view/inbox/message >[2=1]",
                    "echo wrong-passphrase-123 >/tmp/mailmnt/0/view/unlock",
                    "cat /tmp/mailmnt/0/view/status",
                    "echo correct-passphrase-123 >/tmp/mailmnt/0/view/unlock",
                    "echo $mid >/tmp/mailmnt/0/view/inbox/select",
                    "cat /tmp/mailmnt/0/view/status",
                    "cat /tmp/mailmnt/0/view/inbox/message",
                    "echo imaps >/tmp/mailmnt/0/view/account/protocol",
                    "echo mail.example.test >/tmp/mailmnt/0/view/account/host",
                    "echo alice@example.test >/tmp/mailmnt/0/view/account/user",
                    "echo INBOX >/tmp/mailmnt/0/view/account/folder",
                    "echo save >/tmp/mailmnt/0/view/account/save",
                    "cat /tmp/mailmnt/0/view/account/current.tab",
                    "grep mail.example.test /tmp/o9mail-private/alice/account.enc >/tmp/account-leak",
                    "wc -c /tmp/account-leak",
                    "mkdir /tmp/fakeupas", "mkdir /tmp/fakeupas/fixture",
                    "mkdir /tmp/fakeupas/fixture/42",
                    "echo 'Subject: imported-otter-3741' >/tmp/fakeupas/fixture/42/raw",
                    "echo 0123456789abcdef0123456789abcdef01234567 >/tmp/fakeupas/fixture/42/digest",
                    "/tmp/upas-sync sync /tmp/mailmnt/0/view /tmp/fakeupas wrong.example.test fixture",
                    "/tmp/upas-sync sync /tmp/mailmnt/0/view /tmp/fakeupas mail.example.test fixture",
                    "cat /tmp/mailmnt/0/view/inbox/last",
                    "cat /tmp/mailmnt/0/view/inbox/origin",
                    "cat /tmp/mailmnt/0/view/inbox/message",
                    "/tmp/upas-sync sync /tmp/mailmnt/0/view /tmp/fakeupas mail.example.test fixture",
                    "cat /tmp/mailmnt/0/view/inbox/last",
                    "cat /tmp/mailmnt/0/view/inbox/origin",
                    "cat /tmp/mailmnt/0/view/inbox/index.tab",
                    "cat /tmp/mailmnt/0/view/inbox/message",
                    "ls /tmp/o9mail-private/alice/*.enc | wc -l",
                    "ip/ipconfig ether /net/ether0 10.0.2.15 255.255.255.0 10.0.2.2",
                    "mkdir /tmp/o9mail-trust",
                    "cp /n/9fat/o9mail/mail-trust /tmp/o9mail-trust/mail",
                    "bind -b /tmp/o9mail-trust /sys/lib/tls",
                    f"echo 'key proto=pass service=imap server={IMAP_HOST} user={IMAP_LOGIN} !password={IMAP_PASSWORD}' >/mnt/factotum/ctl",
                    "upas/fs -np -m /mail/fs",
                    "echo imaps >/tmp/mailmnt/0/view/account/protocol",
                    f"echo {IMAP_HOST} >/tmp/mailmnt/0/view/account/host",
                    f"echo {IMAP_LOGIN} >/tmp/mailmnt/0/view/account/user",
                    "echo INBOX >/tmp/mailmnt/0/view/account/folder",
                    "echo save >/tmp/mailmnt/0/view/account/save",
                    f"/tmp/upas-sync sync /tmp/mailmnt/0/view /mail/fs {IMAP_HOST}",
                    "cat /tmp/mailmnt/0/view/inbox/index.tab >/tmp/live-index.tab",
                    "for(id in `{awk '/^id=/{sub(/^id=/,\"\");print}' /tmp/live-index.tab}) { echo $id >/tmp/mailmnt/0/view/inbox/select; cat /tmp/mailmnt/0/view/inbox/message }",
                    f"/tmp/upas-sync sync /tmp/mailmnt/0/view /mail/fs {IMAP_HOST}",
                    "cat /tmp/mailmnt/0/view/inbox/last",
                ):
                    guest(command, 300 if "mk libo9" in command else
                          60 if "upas-sync sync" in command and "/mail/fs" in command else 180)
                transcript = logpath.read_text(errors="replace")
                if "ENROLL READY" not in transcript or "RESPONSE READY" not in transcript:
                    raise AssertionError("enrollment or AppUser proof failed")
                if "binary roundtrip: 7 6d61696c00ff2a" not in transcript:
                    raise AssertionError("Vault did not preserve binary plaintext")
                if "state=verified" not in transcript or "principal=alice" not in transcript:
                    raise AssertionError("AppUser principal not verified")
                if not re.search(
                    r"term% cat /tmp/othermnt/0/view/principal.*?permission denied",
                    transcript, re.S | re.I
                ):
                    raise AssertionError("other attach accessed Alice's view")
                statuses = re.findall(
                    r"term% cat /tmp/mailmnt/0/view/status; echo MAILEND\d+\n"
                    r"(locked|unlocked)\n", transcript
                )
                if statuses != ["unlocked", "locked", "locked", "unlocked"]:
                    raise AssertionError(f"unexpected lock transitions: {statuses}")
                if "secret-owl-5937" not in transcript:
                    raise AssertionError("decrypted message unavailable")
                if not re.search(r"[0-9a-f]{32}\.enc", transcript):
                    raise AssertionError("ciphertext message missing")
                if not re.search(r"term% wc -c /tmp/mail-leak.*?\n\s*0\s+/tmp/mail-leak", transcript, re.S):
                    raise AssertionError("plaintext appeared in ciphertext")
                if not re.search(r"term% wc -c /tmp/account-leak.*?\n\s*0\s+/tmp/account-leak", transcript, re.S):
                    raise AssertionError("account settings appeared in ciphertext")
                if "host=mail.example.test" not in transcript:
                    raise AssertionError("encrypted account did not round trip")
                if "stored " not in transcript or "duplicate " not in transcript:
                    raise AssertionError("UPAS import did not deduplicate")
                if "upas-sync: invalid account or unexpected host" not in transcript:
                    raise AssertionError("UPAS import accepted an unexpected host")
                if not re.search(
                    r"term% ls /tmp/o9mail-private/alice/\*\.enc \| wc -l; echo MAILEND\d+\n"
                    r"\s*4\s*\n", transcript
                ):
                    raise AssertionError("duplicate sync changed message count")
                if not re.search(
                    r"term% cat /tmp/mailmnt/0/view/inbox/message; echo MAILEND\d+\n"
                    r"Subject: imported-otter-3741",
                    transcript
                ):
                    raise AssertionError("UPAS fixture was not processed")
                if "live-imaps-finch-7043" not in transcript or "live-imaps-heron-8261" not in transcript:
                    raise AssertionError("live IMAPS messages were not imported")
                live_runs = re.findall(
                    r"term% /tmp/upas-sync sync /tmp/mailmnt/0/view /mail/fs "
                    r"10\.0\.2\.100; echo MAILEND\d+\n(.*?)MAILEND\d+",
                    transcript, re.S
                )
                if len(live_runs) != 2 or "1 stored " not in live_runs[0] or \
                   "2 stored " not in live_runs[0] or "1 duplicate " not in live_runs[1] or \
                   "2 duplicate " not in live_runs[1]:
                    raise AssertionError(f"live IMAPS dedup failed: {live_runs}")
                if imaps.commands.count("login") < 1 or imaps.commands.count("uid") < 2:
                    raise AssertionError(f"UPAS did not fetch live IMAPS bodies: {imaps.commands}")
                if imaps.errors:
                    raise AssertionError(f"IMAPS server errors: {imaps.errors}")
                print("O9MAIL VM PASS: AppUser, encrypted account, offline and live IMAPS import, dedup")
            except Exception:
                log.flush()
                print(logpath.read_text(errors="replace")[-18000:])
                raise
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                imaps.close()


if __name__ == "__main__":
    main()
