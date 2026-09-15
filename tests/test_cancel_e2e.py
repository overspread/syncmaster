"""End-to-end cancellation test: drive the REAL start_sync/cancel_sync path.

Uses a fake `rsync` executable on PATH so no SSH, no real remote is ever
touched. The fake rsync creates a partial file (simulating an interrupted
transfer) and then blocks long enough to be cancelled.

Verifies:
  1. cancel_sync() returns success
  2. the rsync subprocess and its entire session (incl. a "child" proxying
     the ssh helper) are actually gone -> no residual subprocesses
  3. the run thread finishes and releases sync_running (lock not leaked)
  4. the partial artifact is retained so a retry can resume
"""
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from fake_rsync import FAKE_RSYNC

_SANDBOX = tempfile.TemporaryDirectory()
_old_home = os.environ.get('HOME')
os.environ['HOME'] = _SANDBOX.name
_spec = importlib.util.spec_from_file_location('sync_server', ROOT / 'backend/server.py')
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
if _old_home is not None:
    os.environ['HOME'] = _old_home


class CancelSyncEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory()
        r = Path(cls.root.name)
        cls.bin = r / 'bin'
        cls.src = r / 'src'
        cls.dst = r / 'dst'
        cls.key = r / 'dummy.pem'
        cls.bin.mkdir(); cls.src.mkdir(); cls.dst.mkdir()
        cls.key.write_text('not-a-real-key\n')
        fake = cls.bin / 'rsync'
        fake.write_text(FAKE_RSYNC)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        cls.old_path = os.environ['PATH']
        os.environ['PATH'] = str(cls.bin) + ':' + cls.old_path

        # Keep the test hermetic: no backup copies, no SSH config writing.
        cls.orig_create_backup = server._create_backup
        server._create_backup = lambda local_dir: None
        cls.orig_cleanup_backups = server._cleanup_old_backups
        server._cleanup_old_backups = lambda: None
        cls.orig_ssh_cmd = server._ssh_cmd
        server._ssh_cmd = lambda cfg: ("ssh -F /dev/null", "/dev/null")

    @classmethod
    def tearDownClass(cls):
        server._create_backup = cls.orig_create_backup
        server._cleanup_old_backups = cls.orig_cleanup_backups
        server._ssh_cmd = cls.orig_ssh_cmd
        os.environ['PATH'] = cls.old_path
        cls.root.cleanup()

    def test_cancel_stops_rsync_tree_and_keeps_partial(self):
        (self.src / 'payload.bin').write_bytes(b'payload body')
        server.db.save_category({
            "id": "e2e", "name": "e2e-task", "localPath": str(self.src),
            "remotePath": "/home/opc/test/e2e", "mode": "toServer",
            "isEnabled": True, "lastSync": "-", "files": [],
            "deletePolicy": "noDelete",
        })
        server.db.save_config({
            "backupEnabled": "0",
            "useJumpHost": "0",
            "remoteKey": str(self.key),
        })
        # The fake rsync ignores `remote:` targets, so tell it where to land.
        self.child_marker = str(Path(self.root.name) / 'child_pids')
        os.environ['FAKE_RSYNC_DST'] = str(self.dst)
        os.environ['FAKE_RSYNC_CHILD_PIDS'] = self.child_marker
        open(self.child_marker, 'w').close()

        ok, msg = server.start_sync('e2e', 'push')
        self.assertTrue(ok, msg)

        # Wait for the real rsync Popen to register as sync_process.
        deadline = time.time() + 15
        while time.time() < deadline and server.sync_process is None:
            time.sleep(0.05)
        self.assertIsNotNone(server.sync_process, 'run_sync_impl never launched rsync')
        proc = server.sync_process

        # Sync on the fake rsync having spawned its child, so the residual check
        # below is not racing the child spawn.
        deadline = time.time() + 15
        while time.time() < deadline:
            if Path(self.child_marker).read_text().strip():
                break
            time.sleep(0.02)
        self.assertTrue(Path(self.child_marker).read_text().strip(),
                        'fake rsync never spawned its child')

        ok, msg = server.cancel_sync()
        self.assertTrue(ok, msg)

        # The run thread must unwind and release sync_running.
        self.assertTrue(server.sync_running, 'sync_running not set')
        deadline = time.time() + 15
        while time.time() < deadline and server.sync_running:
            time.sleep(0.05)
        self.assertFalse(server.sync_running, 'sync_running leaked after cancel')

        # rsync itself is dead.
        proc.wait(timeout=15)
        self.assertIsNotNone(proc.returncode)
        self.assertNotEqual(proc.returncode, 0, 'cancelled rsync reported success')

        # The "ssh helper" child spawned inside the same session must be gone
        # too -- this is the residual-subprocess guarantee. We assert on the
        # exact PIDs the fake rsync reported, so there is no reliance on
        # pgrep full-command matching (which can hit unrelated processes).
        time.sleep(0.5)
        with open(self.child_marker) as f:
            pids = [int(x.strip()) for x in f.read().split() if x.strip()]
        self.assertTrue(pids, 'fake rsync reported no child pid; test is vacuous')
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            alive.append(pid)
        self.assertEqual(alive, [],
                         'residual child processes still alive: %s' % alive)

        # Partial retained -> a retry can resume instead of restarting.
        partials = list((self.dst / '.rsync-partial').glob('*')) if (self.dst / '.rsync-partial').is_dir() else []
        self.assertTrue(partials, 'partial artifact not retained after cancellation')

        # A fresh run must be startable again (no lock state leaked).
        open(self.child_marker, 'w').close()  # reset: read only this run's child
        ok, msg = server.start_sync('e2e', 'push')
        self.assertTrue(ok, msg)
        deadline = time.time() + 15
        while time.time() < deadline and server.sync_process is None:
            time.sleep(0.05)
        if server.sync_process:
            server.cancel_sync()
            server.sync_process.wait(timeout=15)
        os.environ.pop('FAKE_RSYNC_DST', None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
