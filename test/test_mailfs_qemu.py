#!/usr/bin/env python3
"""Exercise AppUser login, Vault storage, and restart unlock in a 9front VM."""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


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
        for name in ("mailfs", "enroll", "respond"):
            with (work / f"{name}.c").open("w") as output:
                subprocess.run([str(O9 / "o9c/o9c"), f"{name}.o9"],
                               cwd=PROJECT, stdout=output, check=True)
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
                           ("mailfs.c", "enroll.c", "respond.c")):
            subprocess.run(["mcopy", "-i", fat, str(path),
                            f"::o9mail/{name}"], check=True)
        for name in runtime:
            subprocess.run(["mcopy", "-i", fat, str(O9 / name),
                            f"::o9mail/{name}"], check=True)
        for path in (O9 / "libtab").glob("*.[ch]"):
            subprocess.run(["mcopy", "-i", fat, str(path),
                            f"::o9mlib/{path.name}"], check=True)

        qemu = ["qemu-system-i386", "-m", "1024", "-display", "none",
                "-monitor", "none", "-serial", "stdio", "-no-reboot",
                "-netdev", "user,id=n0", "-device", "ne2k_pci,netdev=n0",
                "-drive", f"file={disk},format=raw,if=ide,index=0"]
        if os.access("/dev/kvm", os.R_OK | os.W_OK):
            qemu.insert(1, "-enable-kvm")
        logpath = work / "serial.log"
        with logpath.open("w") as log:
            proc = subprocess.Popen(qemu, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
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
                    "8l -o /tmp/mailfs /tmp/o9mail/mailfs.8 /tmp/o9mail/libo9.a /386/lib/libndb.a /386/lib/libauth.a /386/lib/libmp.a /386/lib/libsec.a",
                    "8l -o /tmp/mail-enroll /tmp/o9mail/enroll.8 /tmp/o9mail/libo9.a /386/lib/libndb.a /386/lib/libauth.a /386/lib/libmp.a /386/lib/libsec.a",
                    "8l -o /tmp/mail-respond /tmp/o9mail/respond.8 /tmp/o9mail/libo9.a /386/lib/libndb.a /386/lib/libauth.a /386/lib/libmp.a /386/lib/libsec.a",
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
                ):
                    guest(command, 300 if "mk libo9" in command else 180)
                transcript = logpath.read_text(errors="replace")
                if "ENROLL READY" not in transcript or "RESPONSE READY" not in transcript:
                    raise AssertionError("enrollment or AppUser proof failed")
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
                print("O9MAIL VM PASS: AppUser proof, encrypted write, lock and reopen")
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


if __name__ == "__main__":
    main()
