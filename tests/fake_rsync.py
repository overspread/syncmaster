"""Shared fake rsync scripts for test isolation.

Both scripts are Python executables placed on PATH as ``rsync`` so tests
never touch SSH or a real remote server.

FAKE_RSYNC: leaves a ``.rsync-partial`` fragment, spawns a child ``sleep``
(simulating rsync's ssh helper), writes the child PID to a marker file
(when ``FAKE_RSYNC_CHILD_PIDS`` is set), then blocks until cancelled.

FAKE_RSYNC_BIDIR: records the command line + ``--files-from`` manifest to
a log file (``FAKE_RSYNC_LOG``), then exits 0 immediately.  Used to verify
that push/pull two-phase ``--files-from`` lists are independent.
"""

FAKE_RSYNC = r'''#!/usr/bin/env python3
"""Fake rsync: leave a partial file, spawn one child, then block.

Destination comes from FAKE_RSYNC_DST so this never depends on how rsync is
invoked (`remote:/path/` is not a local path). The spawned `sleep` simulates
rsync's ssh helper: same process group, so it must die with the group kill.
Its PID is written to FAKE_RSYNC_CHILD_PIDS so the test can assert on the
exact child instead of relying on pgrep full-command matching.
"""
import os, subprocess, sys, time
dst = os.environ.get('FAKE_RSYNC_DST')
if dst:
    d = os.path.join(dst, '.rsync-partial')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'payload.bin'), 'wb') as f:
        f.write(b'\x00' * 4096)
proc = subprocess.Popen(['sleep', '60'])
marker = os.environ.get('FAKE_RSYNC_CHILD_PIDS')
if marker:
    with open(marker, 'a') as f:
        f.write(str(proc.pid) + '\n')
sys.stderr.write('fake-rsync running\n'); sys.stderr.flush()
time.sleep(60)
'''

FAKE_RSYNC_BIDIR = r'''#!/usr/bin/env python3
"""Fake rsync: log the command line + --files-from manifest, then exit 0."""
import os, sys

log = os.environ.get('FAKE_RSYNC_LOG')
argv = sys.argv[1:]
entries = ''
manifest = next((a[len('--files-from='):] for a in argv if a.startswith('--files-from=')), None)
if manifest and os.path.exists(manifest):
    with open(manifest, encoding='utf-8') as f:
        entries = f.read().replace('\n', '|').strip('|')
with open(log, 'a', encoding='utf-8') as f:
    f.write(' '.join(a for a in argv if not a.startswith('--files-from='))
            + '  @@FILES@@' + entries + '\n')
'''
