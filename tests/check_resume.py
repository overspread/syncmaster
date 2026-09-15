"""Local, isolated rsync interruption/recovery integration check.

Builds the rsync command from server._transfer_flags() so this validates the
real production flags (including --no-whole-file), not a hand-copied list.
All transfers are strictly local (dir/ <-> dir/); no SSH, no remote host.
"""
import hashlib
import importlib.util
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = tempfile.TemporaryDirectory()
old_home = os.environ.get('HOME')
os.environ['HOME'] = SANDBOX.name
spec = importlib.util.spec_from_file_location('sync_server', ROOT / 'backend/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
if old_home is not None:
    os.environ['HOME'] = old_home

with tempfile.TemporaryDirectory(prefix='syncmaster-resume-') as tmp:
    root = Path(tmp)
    src, dst = root / 'src', root / 'dst'
    src.mkdir(); dst.mkdir()
    # Payload sized so that with the heavy bandwidth cap below the transfer
    # outlives the SIGTERM; a 4MB file completes before we can interrupt it.
    data = os.urandom(64 * 1024 * 1024)
    (src / 'payload.bin').write_bytes(data)
    # Seed an existing destination: cancellation must not destroy it.
    (dst / 'payload.bin').write_bytes(b'original')

    # production flags, plus a hard rate cap so the transfer outlasts the kill
    flags = server._transfer_flags('push', False)
    flags = [f for f in flags if not f.startswith('--bwlimit')] + ['--bwlimit=200']
    cmd = ['rsync'] + flags + [str(src) + '/', str(dst) + '/']

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)
    # Interrupt mid-transfer. Wait for rsync to have written a partial file
    # (with --no-whole-file it appears immediately) so the interruption is
    # guaranteed to land inside the transfer rather than after a clean exit.
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if (dst / '.rsync-partial').is_dir() and list((dst / '.rsync-partial').glob('*')):
            break
        time.sleep(0.05)
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
    proc.communicate(timeout=20)
    pieces = list((dst / '.rsync-partial').glob('*'))
    assert pieces, 'No partial file retained after interruption'
    assert (dst / 'payload.bin').read_bytes() == b'original', 'Original destination damaged'
    print('Interrupted transfer: original intact; partial bytes:', pieces[0].stat().st_size)

    # Resume with the production flags, unthrottled -> the partial is reused
    resume = [f for f in flags if not f.startswith('--bwlimit')]
    result = subprocess.run(['rsync'] + resume + [str(src) + '/', str(dst) + '/'],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    got = hashlib.sha256((dst / 'payload.bin').read_bytes()).digest()
    want = hashlib.sha256(data).digest()
    assert got == want, 'resume produced a different file than the source'
    print('Resumed transfer: SHA-256 matches; exit=0')
    print(result.stdout)
