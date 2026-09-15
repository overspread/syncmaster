import importlib.util
import os
import subprocess
from pathlib import Path
import tempfile
import unittest

# Import with an isolated HOME: never touch real sync settings or files.
ROOT = Path(__file__).resolve().parents[1]
SANDBOX = tempfile.TemporaryDirectory()
old_home = os.environ.get('HOME')
os.environ['HOME'] = SANDBOX.name
spec = importlib.util.spec_from_file_location('sync_server', ROOT / 'backend/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
if old_home is not None:
    os.environ['HOME'] = old_home

class TransferTests(unittest.TestCase):
    def test_unknown_direction_rejected_before_task_lookup(self):
        ok, message = server.start_sync('missing', 'toServer')
        self.assertFalse(ok)
        self.assertIn('方向', message)

    def test_bidirectional_never_deletes_and_uses_update(self):
        flags = server._transfer_flags('bidirectional', False)
        self.assertNotIn('--delete', flags)
        self.assertIn('--update', flags)
        self.assertIn('--partial-dir=.rsync-partial', flags)

    def test_selected_files_never_delete(self):
        self.assertNotIn('--delete', server._transfer_flags('push', True))

    def test_full_one_way_preserves_mirror_behavior(self):
        self.assertIn('--delete', server._transfer_flags('push', False))

    def test_unsafe_paths_rejected(self):
        for path in ['/home/opc/x/..', '/home/opc/./', '/a/../home', 'relative/path/dir']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                server._validate_remote_dir(path)

    def test_transfer_flags_enable_incremental_resume(self):
        # --no-whole-file 强制增量传输，中断后才能真正从残片续传。
        for direction in ('push', 'pull', 'bidirectional'):
            with self.subTest(direction=direction):
                flags = server._transfer_flags(direction, False)
                self.assertIn('--no-whole-file', flags)
                self.assertIn('--partial', flags)
                self.assertIn('--partial-dir=.rsync-partial', flags)

    def test_bidirectional_pull_side_never_deletes(self):
        # 双向是 push 后再 pull；pull 侧若带 --delete 会按远端镜像误删本地文件。
        flags = server._transfer_flags('bidirectional', False)
        self.assertNotIn('--delete', flags)

    def test_bidirectional_dry_run_matches_transfer_flags(self):
        # dry-run 必须与真实同步同一套 flag 规则，否则"预计删除 N 个"是谎报。
        import inspect
        src = inspect.getsource(server.get_sync_diff)
        self.assertIn('_transfer_flags', src)
        self.assertIn('_mode_to_direction', src)
        # 不能再有无条件硬编码的 --delete
        self.assertNotIn('"--delete"', src)

    def test_mode_to_direction_maps_db_modes(self):
        # 数据库 mode 与 rsync 方向口径不同；必须映射，否则 'toServer' 会
        # 落进 else 分支被错误加上 --delete。
        self.assertEqual(server._mode_to_direction('toServer'), 'push')
        self.assertEqual(server._mode_to_direction('toLocal'), 'pull')
        self.assertEqual(server._mode_to_direction('bidirectional'), 'bidirectional')
        self.assertEqual(server._mode_to_direction('unknown'), 'bidirectional')

    def test_dry_run_flags_per_task_mode(self):
        # 模拟 get_sync_diff 对每个任务 mode 生成 dry-run flag 的逻辑。
        def dry(mode):
            return server._transfer_flags(server._mode_to_direction(mode), False)
        self.assertIn('--delete', dry('toServer'))    # 单向镜像：与真实同步一致
        self.assertIn('--delete', dry('toLocal'))     # 单向镜像：与真实同步一致
        self.assertNotIn('--delete', dry('bidirectional'))

    def test_local_partial_cleanup_removes_only_rsync_artifacts(self):
        root = tempfile.TemporaryDirectory()
        try:
            r = Path(root.name)
            (r / '.rsync-partial').mkdir()
            (r / '.rsync-partial' / 'payload.bin').write_bytes(b'x')
            (r / '.rsync.XXXXXX').write_bytes(b'partial temp')
            (r / '.git').mkdir()
            (r / 'keep.txt').write_bytes(b'keep me')
            (r / 'keep_dir').mkdir()
            server._cleanup_local_partial(str(r))
            names = set(os.listdir(r))
            self.assertNotIn('.rsync-partial', names)
            self.assertNotIn('.rsync.XXXXXX', names)
            # 普通用户数据绝不受影响
            self.assertIn('.git', names)
            self.assertIn('keep.txt', names)
            self.assertEqual((r / 'keep_dir').exists(), True)
        finally:
            root.cleanup()

    def test_terminate_proc_group_kills_child_session(self):
        p = subprocess.Popen(['sleep', '300'], start_new_session=True)
        try:
            server._terminate_proc_group(p, term_timeout=3.0, kill_timeout=2.0)
            self.assertIsNotNone(p.poll(), 'child survived cancellation')
        finally:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
