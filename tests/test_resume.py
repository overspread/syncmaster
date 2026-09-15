"""断点续传优化测试（worktree A：wt/syncmaster-resume）。

全部本地执行：不 SSH、不 rsync 真实服务器。涉及子进程的地方要么用
fake rsync（自己写的脚本，留在临时 PATH 里），要么用真实 rsync 的
本地目录对目录传输（本地临时目录之间）。

覆盖四件事：
  1. 中断恢复：SIGTERM 中断后 partial 保留、原文件无损、续传后哈希一致
  2. 哈希一致：断点续传的最终内容与源一致
  3. 取消清理：成功后清掉本地/远端续传残片，且不碰用户数据
  4. 双向不误删：双向绝不 --delete，且 .rsync-partial 被排除
"""
import hashlib
import importlib.util
import json
import os
import shutil
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
from fake_rsync import FAKE_RSYNC, FAKE_RSYNC_BIDIR
_server = ROOT / 'backend' / 'server.py'
if not _server.exists():
    _server = ROOT / 'syncmaster' / 'server.py'

# 隔离 HOME：绝不触碰真实同步配置与用户数据
_SBX = tempfile.TemporaryDirectory()
_old_home = os.environ.get('HOME')
os.environ['HOME'] = _SBX.name
_spec = importlib.util.spec_from_file_location('sync_server', _server)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
if _old_home is not None:
    os.environ['HOME'] = _old_home


class TransferFlagsTests(unittest.TestCase):
    """双向不误删 + .rsync-partial 排除规则。"""

    def test_bidirectional_never_deletes_and_uses_update(self):
        flags = server._transfer_flags('bidirectional', False)
        self.assertNotIn('--delete', flags, '双向不能 --delete：会镜像删除另一侧独有文件')
        self.assertIn('--update', flags)

    def test_selected_files_never_delete(self):
        self.assertNotIn('--delete', server._transfer_flags('push', True))

    def test_full_one_way_keeps_mirror_semantics(self):
        self.assertIn('--delete', server._transfer_flags('push', False))

    def test_partial_dir_is_excluded_on_every_direction(self):
        # 不排除的话，远端残留的 .rsync-partial/* 会在下一次 pull 时被当普通文件
        # 同步回本地，--delete 还会误判本地文件为多余而删掉。
        for direction in ('push', 'pull', 'bidirectional'):
            for selected in (False, True):
                with self.subTest(direction=direction, selected=selected):
                    flags = server._transfer_flags(direction, selected)
                    self.assertIn('--exclude', flags)
                    self.assertIn('.rsync-partial', flags)

    def test_resume_flags_present(self):
        # 断点续传的最小集合：--partial 保留残片，--no-whole-file 走增量传输
        for direction in ('push', 'pull', 'bidirectional'):
            with self.subTest(direction=direction):
                flags = server._transfer_flags(direction, False)
                for f in ('--partial', '--partial-dir=.rsync-partial', '--no-whole-file'):
                    self.assertIn(f, flags, '缺少断点续传必要 flag: %s' % f)

    def test_dry_run_shares_transfer_flag_rules(self):
        # dry-run 若与真实同步不同 flag，"预计删除 N 个"就是谎报。
        import inspect
        src = inspect.getsource(server.get_sync_diff)
        self.assertIn('_transfer_flags', src)
        self.assertNotIn('"--delete"', src)


class RemoteDirValidationTests(unittest.TestCase):
    def test_unsafe_paths_rejected(self):
        for path in ('/home/opc/x/..', '/home/opc/./', '/a/../home',
                     'relative/path/dir', '/home', '/home/opc', '/tmp'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                server._validate_remote_dir(path)

    def test_safe_paths_pass_through_normalised(self):
        self.assertEqual(server._validate_remote_dir('/home/opc/.hermes'), '/home/opc/.hermes')
        self.assertEqual(server._validate_remote_dir('/home/opc/.hermes/'), '/home/opc/.hermes')


class PartialCleanupTests(unittest.TestCase):
    def test_local_cleanup_removes_only_rsync_artifacts(self):
        r = tempfile.TemporaryDirectory()
        try:
            root = Path(r.name)
            (root / '.rsync-partial').mkdir()
            (root / '.rsync-partial' / 'payload.bin').write_bytes(b'x')
            (root / '.rsync.AB12CD').write_bytes(b'partial temp')
            (root / '.git').mkdir()
            (root / 'keep.txt').write_bytes(b'keep me')
            (root / 'keep_dir').mkdir()
            server._cleanup_local_partial(str(root))
            names = set(os.listdir(root))
            self.assertNotIn('.rsync-partial', names)
            self.assertNotIn('.rsync.AB12CD', names)
            # 普通用户数据绝不受影响
            self.assertIn('.git', names)
            self.assertIn('keep.txt', names)
            self.assertEqual((root / 'keep_dir').exists(), True)
        finally:
            r.cleanup()

    def test_remote_cleanup_issues_bounded_rm(self):
        # 远端清理失败必须静默，且命令必须带路径护栏。
        calls = []
        server._run_remote_cmd = lambda cfg, cmd, timeout=10: calls.append(cmd) or (False, '', 'ssh refused')
        self.assertIsNone(server._cleanup_remote_partial({}, '/home/opc/.hermes'))
        self.assertEqual(len(calls), 1)
        self.assertIn('cd "/home/opc/.hermes"', calls[0])
        self.assertIn('.rsync-partial', calls[0])


class CheckpointTests(unittest.TestCase):
    """应用层续传点：中断的任务/方向/文件选择要记得住。"""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        server.CHECKPOINT_PATH = Path(self.d.name) / 'resume_checkpoint.json'

    def tearDown(self):
        self.d.cleanup()

    def test_checkpoint_roundtrip(self):
        cp = {'categoryId': 'core', 'categoryName': '核心配置',
              'direction': 'push', 'files': ['a.txt', 'b.json']}
        server._save_checkpoint(cp)
        got = server._load_checkpoint()
        self.assertEqual(got['categoryId'], 'core')
        self.assertEqual(got['files'], ['a.txt', 'b.json'])
        state = server.get_resume_state()
        self.assertTrue(state['resumable'])
        self.assertEqual(state['direction'], 'push')

    def test_clear_removes_checkpoint(self):
        server._save_checkpoint({'categoryId': 'x', 'direction': 'pull', 'files': []})
        self.assertTrue(server.clear_resume_checkpoint())
        self.assertFalse(server.get_resume_state()['resumable'])

    def test_corrupt_checkpoint_is_not_resumable(self):
        server.CHECKPOINT_PATH.write_text('{not json')
        self.assertFalse(server.get_resume_state()['resumable'])
        server.CHECKPOINT_PATH.write_text('{"direction": "push"}')
        self.assertFalse(server.get_resume_state()['resumable'])

    def test_no_checkpoint_means_not_resumable(self):
        server.CHECKPOINT_PATH.unlink(missing_ok=True)
        self.assertFalse(server.get_resume_state()['resumable'])


class SplitSelectedTests(unittest.TestCase):
    def test_split_by_local_presence(self):
        d = tempfile.TemporaryDirectory()
        try:
            root = Path(d.name)
            (root / 'a.txt').write_bytes(b'a')
            (root / 'sub').mkdir()
            upload, download = server._split_selected(['a.txt', 'missing.txt', 'sub', '  '], str(root))
            self.assertEqual(upload, ['a.txt', 'sub'])
            self.assertEqual(download, ['missing.txt'])
        finally:
            d.cleanup()

    def test_missing_local_dir_splits_to_download(self):
        upload, download = server._split_selected(['a.txt'], '/no/such/dir/anywhere')
        self.assertEqual(upload, [])
        self.assertEqual(download, [])


class ManifestTests(unittest.TestCase):
    def test_empty_manifest_returns_none(self):
        self.assertIsNone(server._files_from_manifest([]))
        self.assertIsNone(server._files_from_manifest(['', '  ']))

    def test_manifest_written_and_cleaned(self):
        p = server._files_from_manifest(['a.txt', 'sub/b.json'])
        self.assertIsNotNone(p)
        self.assertTrue(os.path.exists(p))
        try:
            self.assertEqual(open(p, encoding='utf-8').read().splitlines(),
                             ['a.txt', 'sub/b.json'])
        finally:
            os.unlink(p)


class SleepCancellableTests(unittest.TestCase):
    def test_sleep_returns_false_when_not_cancelled(self):
        self.assertFalse(server._sleep_cancellable(0.15))

    def test_sleep_interrupted_by_cancel(self):
        server.sync_cancelled = True
        t0 = time.time()
        self.assertTrue(server._sleep_cancellable(5))
        self.assertLess(time.time() - t0, 1.5, '取消应该立即打断等待')
        server.sync_cancelled = False


class DirDisplayTests(unittest.TestCase):
    def test_all_directions(self):
        self.assertEqual(server._dir_display('push'), '本地 → 服务器')
        self.assertEqual(server._dir_display('pull'), '服务器 → 本地')
        self.assertEqual(server._dir_display('bidirectional'), '双向同步')
        self.assertEqual(server._dir_display('weird'), 'weird')


class TerminateProcGroupTests(unittest.TestCase):
    """取消清理：rsync 的 ssh 子进程不能残留。"""

    def test_kills_child_session(self):
        p = subprocess.Popen(['sleep', '300'], start_new_session=True)
        try:
            server._terminate_proc_group(p, term_timeout=3.0, kill_timeout=2.0)
            self.assertIsNotNone(p.poll(), '子进程在取消后仍然存活')
        finally:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)

    def test_dead_process_does_not_raise(self):
        p = subprocess.Popen(['true'])
        p.wait()
        self.assertEqual(server._terminate_proc_group(p).returncode, 0)


class FilesFromEndToEndTests(unittest.TestCase):
    """精准文件模式 + 双向：勾选的文件一端不存在时不能整体失败。

    旧实现把同一份 --files-from 清单用于 push 和 pull，pull 阶段源在远端
    且该文件本地不存在，rsync 直接 exit 23/24 被当成同步失败。
    """

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory()
        r = Path(cls.root.name)
        cls.bin = r / 'bin'
        cls.src = r / 'src'
        cls.bin.mkdir()
        cls.src.mkdir()
        cls.key = r / 'dummy.pem'
        cls.key.write_text('not-a-real-key\n')
        cls.fake = cls.bin / 'rsync'
        cls.fake.write_text(FAKE_RSYNC_BIDIR)
        cls.fake.chmod(cls.fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        cls.old_path = os.environ['PATH']
        os.environ['PATH'] = str(cls.bin) + ':' + cls.old_path

        cls.orig_create_backup = server._create_backup
        cls.orig_cleanup_backups = server._cleanup_old_backups
        cls.orig_ssh_cmd = server._ssh_cmd
        cls.orig_cleanup_local = server._cleanup_local_partial
        cls.orig_cleanup_remote = server._cleanup_remote_partial
        server._create_backup = lambda local_dir: None
        server._cleanup_old_backups = lambda: None
        server._ssh_cmd = lambda cfg: ('ssh -F /dev/null', '/dev/null')
        server._cleanup_local_partial = lambda d: None
        server._cleanup_remote_partial = lambda cfg, d: None

    @classmethod
    def tearDownClass(cls):
        server._create_backup = cls.orig_create_backup
        server._cleanup_old_backups = cls.orig_cleanup_backups
        server._ssh_cmd = cls.orig_ssh_cmd
        server._cleanup_local_partial = cls.orig_cleanup_local
        server._cleanup_remote_partial = cls.orig_cleanup_remote
        os.environ['PATH'] = cls.old_path
        os.environ.pop('FAKE_RSYNC_LOG', None)
        cls.root.cleanup()

    def test_files_from_split_does_not_fail_when_file_absent_locally(self):
        """精准双向：勾选的文件一端缺失时不能整体失败，且两阶段清单各自独立。

        旧实现把同一份 --files-from 清单同时喂给 push 和 pull；pull 阶段源
        在远端、该文件本地不存在，rsync exit 23/24 被当成同步失败。
        """
        present = self.src / 'present.txt'
        present.write_bytes(b'present body')
        log = Path(self.root.name) / 'rsync_calls.log'
        log.unlink(missing_ok=True)
        os.environ['FAKE_RSYNC_LOG'] = str(log)

        server.db.save_category({
            'id': 'fa', 'name': 'fa-task', 'localPath': str(self.src),
            'remotePath': '/home/opc/test/fa', 'mode': 'bidirectional',
            'isEnabled': True, 'lastSync': '-', 'files': [], 'deletePolicy': 'noDelete',
        })
        server.db.save_config({'backupEnabled': '0', 'useJumpHost': '0',
                               'remoteKey': str(self.key)})

        real_sleep = server._sleep_cancellable
        server._sleep_cancellable = lambda s: False
        try:
            ok, msg = server.start_sync('fa', 'bidirectional',
                                        files=['present.txt', 'only-on-remote.txt'])
            self.assertTrue(ok, msg)
        finally:
            server._sleep_cancellable = real_sleep

        deadline = time.time() + 20
        while time.time() < deadline and server.sync_running:
            time.sleep(0.1)
        self.assertFalse(server.sync_running, '同步线程未收尾')

        self.assertTrue(log.exists(), 'fake rsync 从未被调用')
        calls = [l for l in log.read_text(encoding='utf-8').splitlines() if l.strip()]
        self.assertEqual(len(calls), 2, '精准双向应恰好跑 push + pull 两个阶段: %s' % calls)

        push_call, pull_call = calls
        # push 清单只含本地存在的文件
        self.assertIn('present.txt', push_call)
        self.assertNotIn('only-on-remote.txt', push_call,
                         'push 清单含了本地不存在的文件（会被 rsync exit 23/24）')
        # pull 清单只含本地不存在的文件
        self.assertIn('only-on-remote.txt', pull_call)
        self.assertNotIn('present.txt', pull_call, 'pull 清单含了本地已有的文件')
        # 两阶段方向必须相反：push 是 本地 -> remote，pull 是 remote -> 本地
        remote_end = 'remote:/home/opc/test/fa/'
        local_end = str(self.src) + '/'

        def tail(body):
            return body.split('@@FILES@@')[0].strip().split()[-2:]

        self.assertEqual(tail(push_call), [local_end, remote_end],
                         'push 应为 本地 -> 远端')
        self.assertEqual(tail(pull_call), [remote_end, local_end],
                         'pull 应为 远端 -> 本地')
        # 精准模式两阶段都不能带 --delete
        for c in calls:
            self.assertNotIn('--delete', c)
        os.environ.pop('FAKE_RSYNC_LOG', None)


class CancellationEndToEndTests(unittest.TestCase):
    """取消清理 + 中断恢复：走真实的 start_sync / cancel_sync 路径。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory()
        r = Path(cls.root.name)
        cls.bin = r / 'bin'
        cls.src = r / 'src'
        cls.bin.mkdir()
        cls.src.mkdir()
        cls.key = r / 'dummy.pem'
        cls.key.write_text('not-a-real-key\n')
        cls.fake = cls.bin / 'rsync'
        cls.fake.write_text(FAKE_RSYNC)
        cls.fake.chmod(cls.fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        cls.old_path = os.environ['PATH']
        os.environ['PATH'] = str(cls.bin) + ':' + cls.old_path

        cls.orig_create_backup = server._create_backup
        cls.orig_cleanup_backups = server._cleanup_old_backups
        cls.orig_ssh_cmd = server._ssh_cmd
        server._create_backup = lambda local_dir: None
        server._cleanup_old_backups = lambda: None
        server._ssh_cmd = lambda cfg: ('ssh -F /dev/null', '/dev/null')

    @classmethod
    def tearDownClass(cls):
        server._create_backup = cls.orig_create_backup
        server._cleanup_old_backups = cls.orig_cleanup_backups
        server._ssh_cmd = cls.orig_ssh_cmd
        os.environ['PATH'] = cls.old_path
        cls.root.cleanup()

    def test_cancel_terminates_tree_and_records_resume_point(self):
        (self.src / 'payload.bin').write_bytes(b'payload body')
        server.db.save_category({
            'id': 'e2e', 'name': 'e2e-task', 'localPath': str(self.src),
            'remotePath': '/home/opc/test/e2e', 'mode': 'toServer',
            'isEnabled': True, 'lastSync': '-', 'files': [], 'deletePolicy': 'noDelete',
        })
        server.db.save_config({'backupEnabled': '0', 'useJumpHost': '0',
                               'remoteKey': str(self.key)})

        server.CHECKPOINT_PATH = Path(self.root.name) / 'cp.json'
        server.CHECKPOINT_PATH.unlink(missing_ok=True)

        child_marker = str(Path(self.root.name) / 'child_pids')
        open(child_marker, 'w').close()
        os.environ['FAKE_RSYNC_CHILD_PIDS'] = child_marker

        ok, msg = server.start_sync('e2e', 'push')
        self.assertTrue(ok, msg)

        deadline = time.time() + 15
        while time.time() < deadline and server.sync_process is None:
            time.sleep(0.05)
        self.assertIsNotNone(server.sync_process, 'run_sync_impl 从未启动 rsync')
        proc = server.sync_process

        # Wait for the fake rsync to spawn its child before cancelling.
        deadline = time.time() + 15
        while time.time() < deadline:
            if Path(child_marker).read_text().strip():
                break
            time.sleep(0.02)
        self.assertTrue(Path(child_marker).read_text().strip(),
                        'fake rsync never spawned its child')

        ok, msg = server.cancel_sync()
        self.assertTrue(ok, msg)

        # 线程必须收尾并释放 sync_running（否则用户无法立刻续传）
        deadline = time.time() + 15
        while time.time() < deadline and server.sync_running:
            time.sleep(0.05)
        self.assertFalse(server.sync_running, 'sync_running 在取消后泄漏')

        proc.wait(timeout=15)
        self.assertIsNotNone(proc.returncode)

        # rsync 派生的 "ssh helper" 子进程必须一起死掉
        time.sleep(0.5)
        with open(child_marker) as f:
            pids = [int(x.strip()) for x in f.read().split() if x.strip()]
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            alive.append(pid)
        self.assertEqual(alive, [], '残留子进程: %s' % alive)
        os.environ.pop('FAKE_RSYNC_CHILD_PIDS', None)

        # 应用层续传点必须记下任务与方向
        state = server.get_resume_state()
        self.assertTrue(state['resumable'])
        self.assertEqual(state['categoryId'], 'e2e')
        self.assertEqual(state['direction'], 'push')

        # 放弃续传点后必须消失
        server.clear_resume_checkpoint()
        self.assertFalse(server.get_resume_state()['resumable'])

    def test_cancelled_transfer_can_be_restarted(self):
        # 旧实现在 cancel_sync 里立即 sync_running=False，若用户立刻点"开始"，
        # 旧线程还没收尾 -> 两个 rsync 同时写同一目录。
        (self.src / 'p2.bin').write_bytes(b'x')
        server.db.save_category({
            'id': 'e2e2', 'name': 'e2e-task2', 'localPath': str(self.src),
            'remotePath': '/home/opc/test/e2e2', 'mode': 'toServer',
            'isEnabled': True, 'lastSync': '-', 'files': [], 'deletePolicy': 'noDelete',
        })
        server.db.save_config({'backupEnabled': '0', 'useJumpHost': '0',
                               'remoteKey': str(self.key)})
        server.CHECKPOINT_PATH = Path(self.root.name) / 'cp2.json'

        ok, msg = server.start_sync('e2e2', 'push')
        self.assertTrue(ok, msg)
        deadline = time.time() + 15
        while time.time() < deadline and server.sync_process is None:
            time.sleep(0.05)
        self.assertIsNotNone(server.sync_process)

        self.assertTrue(server.cancel_sync()[0])
        proc = server.sync_process
        proc.wait(timeout=15)

        # 线程已收尾 -> 立刻可以再起一次
        deadline = time.time() + 15
        while time.time() < deadline and server.sync_running:
            time.sleep(0.05)
        self.assertFalse(server.sync_running)

        ok, msg = server.start_sync('e2e2', 'push')
        self.assertTrue(ok, '取消后无法重新启动: %s' % msg)
        deadline = time.time() + 15
        while time.time() < deadline and server.sync_process is None:
            time.sleep(0.05)
        if server.sync_process:
            server.cancel_sync()
            server.sync_process.wait(timeout=15)


class InterruptResumeIntegrationTests(unittest.TestCase):
    """中断恢复 + 哈希一致：真实 rsync，本地临时目录对目录。"""

    def test_interrupt_then_resume_is_byte_identical(self):
        rsync = shutil.which('rsync')
        if not rsync:
            self.skipTest('系统没有 rsync')
        d = tempfile.TemporaryDirectory()
        try:
            root = Path(d.name)
            src, dst = root / 'src', root / 'dst'
            src.mkdir()
            dst.mkdir()
            data = os.urandom(4 * 1024 * 1024)
            (src / 'payload.bin').write_bytes(data)
            # 目标先有旧内容：中断绝不能破坏它
            (dst / 'payload.bin').write_bytes(b'original')

            flags = server._transfer_flags('push', False)
            base = [rsync] + [f for f in flags if f not in ('--progress', '--timeout=300')]
            cmd = base + ['--bwlimit=2048', str(src) + '/', str(dst) + '/']
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    start_new_session=True)
            time.sleep(2)
            server._terminate_proc_group(proc)
            proc.communicate(timeout=15)

            # 中断恢复的前置条件：残片还在、原文件完好
            pieces = list((dst / '.rsync-partial').glob('*')) if (dst / '.rsync-partial').is_dir() else []
            self.assertTrue(pieces, '中断后未保留 partial 残片')
            self.assertEqual((dst / 'payload.bin').read_bytes(), b'original',
                             '中断破坏了目标原有文件')

            # 续传：同一条命令重跑
            rc = subprocess.run([rsync] + [f for f in flags if f not in ('--progress', '--timeout=300')]
                                + [str(src) + '/', str(dst) + '/'],
                                capture_output=True, text=True, timeout=60)
            self.assertEqual(rc.returncode, 0, rc.stderr)
            self.assertEqual(hashlib.sha256((dst / 'payload.bin').read_bytes()).digest(),
                             hashlib.sha256(data).digest(), '续传后内容不一致')

            # 取消清理：成功后残片应被清掉，其他文件不动
            server._cleanup_local_partial(str(dst))
            self.assertFalse((dst / '.rsync-partial').exists(), '成功后未清理 local partial')
            self.assertTrue((dst / 'payload.bin').exists(), '清理误删了正常文件')
        finally:
            d.cleanup()


class StartSyncValidationTests(unittest.TestCase):
    def test_invalid_direction_rejected_before_task_lookup(self):
        ok, msg = server.start_sync('nope', 'toServer')
        self.assertFalse(ok)
        self.assertIn('方向', msg)

    def test_missing_category_rejected(self):
        ok, msg = server.start_sync('definitely-not-a-task', 'push')
        self.assertFalse(ok)
        self.assertIn('未找到任务', msg)


if __name__ == '__main__':
    unittest.main(verbosity=2)
