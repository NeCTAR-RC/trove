# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import os

import unittest
from unittest.mock import MagicMock, patch

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import importutils

LOG = logging.getLogger(__name__)
CONF = cfg.CONF
CONF.backup_encryption_key = None
CONF.backup_id = "backup_unittest"

driver_mapping = {
    'innobackupex': 'backup.drivers.innobackupex.InnoBackupEx',
    'innobackupex_inc': 'backup.drivers.innobackupex.InnoBackupExIncremental',
    'mariabackup': 'backup.drivers.mariabackup.MariaBackup',
    'mariabackup_inc': 'backup.drivers.mariabackup.MariaBackupIncremental',
    'pg_basebackup': 'backup.drivers.postgres.PgBasebackup',
    'pg_basebackup_inc': 'backup.drivers.postgres.PgBasebackupIncremental',
    'xtrabackup': 'backup.drivers.xtrabackup.XtraBackup',
    'xtrabackup_inc': 'backup.drivers.xtrabackup.XtraBackupIncremental'
}


class TestPgBasebackup(unittest.TestCase):
    def setUp(self):
        self.runner_cls = importutils.import_class(
            driver_mapping['pg_basebackup'])
        self.params = {
            'wal_archive_dir': './',
            'filename': '000000010000000000000006.00000168.backup'
        }

        # assertions
        self.assertIsNotNone(self.runner_cls)

    def tearDown(self):
        if os.path.exists(self.params.get('filename')):
            os.remove(self.params.get('filename'))

    def _create_test_data(self):
        with open(self.params.get('filename'), 'w') as file:
            file.write("START WAL LOCATION: -1/3000028 "
                       "(file 000000010000000000000003)\n")
            file.write("STOP WAL LOCATION: 0/3000028 "
                       "(file 000000010000000000000003)\n")
            file.write("CHECKPOINT LOCATION: 0/3000098\n")
            file.write("BACKUP METHOD: streamed\n")
            file.write("BACKUP FROM: master\n")
            file.write("START TIME: 2023-05-01 06:53:41 UTC\n")
            file.write("LABEL: 3070d460-1e67-4fbd-92ca-97c1d0101077\n")
            file.write("START TIMELINE: 1\n")

    def test_instance(self):
        '''Check instance'''
        # call the method
        runner = self.runner_cls(**self.params)

        # assertions
        self.assertIsNotNone(runner)

    def test_cmd(self):
        '''Check cmd property'''
        # call the method
        runner = self.runner_cls(**self.params)

        # assertions
        self.assertEqual(runner.cmd,
                         "pg_basebackup -U postgres -Ft -z "
                         "--wal-method=fetch --label={} "
                         "--pgdata=-".format(self.params.get('filename')))

    def test_manifest(self):
        '''Check manifest'''
        # call the method
        runner = self.runner_cls(**self.params)

        # assertions
        self.assertEqual(runner.manifest,
                         "{}.tar.gz".format(self.params.get('filename')))

    def test_is_read_only(self):
        '''Check is_read_only'''
        # call the method
        runner = self.runner_cls(**self.params)

        # assertions
        runner._is_read_only = True
        self.assertEqual(runner.is_read_only, True)

    def test_get_wal_files(self):
        '''Check get_wal_file'''
        # prepare the test
        runner = self.runner_cls(**self.params)
        recent_backup_file = "000000010000000000000006.00000168.backup"
        last_wal = "000000010000000000000007"
        self._create_test_data()

        runner.get_backup_files = MagicMock(
            return_value=[recent_backup_file])
        with open(last_wal, "w") as file:
            file.write("test")

        # call the method
        ret = runner.get_wal_files()

        # assertions
        self.assertEqual(ret, [last_wal])

        if os.path.exists(last_wal):
            os.remove(last_wal)

    def test_get_backup_files(self):
        '''Check get_backup_file'''
        # prepare the test
        runner = self.runner_cls(**self.params)
        recent_backup_file = "000000010000000000000006.00000168.backup"
        runner.get_backup_files = MagicMock(
            return_value=[recent_backup_file])

        # call the method
        ret = runner.get_backup_files()

        # assertions
        self.assertEqual(ret, [recent_backup_file])

    def test_get_backup_metadata(self):
        '''Check get_backup_metadata'''
        # prepare the test
        runner = self.runner_cls(**self.params)
        runner.label = self.params.get('filename')
        self._create_test_data()

        # call the method
        backup_metadata = runner.get_backup_metadata(
            self.params.get('filename')
        )

        # assertions
        self.assertEqual(backup_metadata['start-segment'], '-1/3000028')
        self.assertEqual(
            backup_metadata['start-wal-file'], '000000010000000000000003'
        )
        self.assertEqual(backup_metadata['stop-segment'], '0/3000028')
        self.assertEqual(
            backup_metadata['stop-wal-file'], '000000010000000000000003')
        self.assertEqual(
            backup_metadata['checkpoint-location'], '0/3000098'
        )
        self.assertEqual(
            backup_metadata['label'], '3070d460-1e67-4fbd-92ca-97c1d0101077'
        )

    def test_get_metadata(self):
        '''Check get_metadata'''
        # prepare the test
        runner = self.runner_cls(**self.params)
        runner.get_metadata = MagicMock(
            return_value={'start-segment': '0/3000028'}
        )

        # call the method
        metadata = runner.get_metadata()

        # assertions
        self.assertEqual(metadata['start-segment'], '0/3000028')

    def test_context(self):
        '''Check context methods'''
        # prepare the test
        runner = self.runner_cls(**self.params)
        runner._is_read_only = True
        runner.pre_backup = MagicMock()
        runner._run = MagicMock()
        runner.post_backup = MagicMock()

        # call the method
        with runner:
            pass

        # assertions
        runner.pre_backup.assert_called_once_with()
        runner._run.assert_called_once_with()
        runner.post_backup.assert_called_once_with()

    def test_check_process(self):
        '''Check check_process'''
        # prepare the test
        runner = self.runner_cls(**self.params)
        runner._is_read_only = True
        runner.start_segment = True
        runner.start_wal_file = True
        runner.stop_segment = True
        runner.stop_wal_file = True
        runner.label = True

        # call the method
        ret = runner.check_process()

        # assertions
        self.assertTrue(ret)

    def test_check_restore_process(self):
        '''Check check_restore_process'''
        # prepare the test
        runner = self.runner_cls(**self.params)
        runner._is_read_only = True
        runner.start_segment = True
        runner.start_wal_file = True
        runner.stop_segment = True
        runner.stop_wal_file = True
        runner.label = True

        # call the method
        ret = runner.check_process()

        # assertions
        self.assertTrue(ret)


class TestPgBasebackupEncryption(unittest.TestCase):
    """Regression coverage for restore_command/incremental_restore_cmd
    previously baking self.decrypt_cmd into the command string that gets
    passed to unpack(), which broke once unpack() started handling
    decryption as its own pipeline stage (rather than a shell-piped
    string prefix).
    """

    def setUp(self):
        self.runner_cls = importutils.import_class(
            driver_mapping['pg_basebackup'])
        self.params = {
            'wal_archive_dir': './',
            'filename': '000000010000000000000006.00000168.backup',
        }

    def _make_runner(self, encrypt_key):
        with patch.object(self.runner_cls, 'encrypt_key', encrypt_key):
            runner = self.runner_cls(**self.params)
        runner.encrypt_key = encrypt_key
        return runner

    def test_restore_command_excludes_decrypt_cmd(self):
        runner = self._make_runner('k3y')

        self.assertEqual(
            'tar xzf - -C /var/lib/postgresql/data/pgdata',
            runner.restore_command)

    def test_incremental_restore_cmd_excludes_decrypt_cmd(self):
        incremental_cls = importutils.import_class(
            driver_mapping['pg_basebackup_inc'])
        with patch.object(incremental_cls, 'encrypt_key', 'k3y'):
            runner = incremental_cls(**self.params)
        runner.encrypt_key = 'k3y'

        self.assertEqual(
            'tar xzf - -C /var/lib/postgresql/data/pgdata',
            runner.incremental_restore_cmd(incr=False))
        self.assertEqual(
            runner.incr_restore_cmd,
            runner.incremental_restore_cmd(incr=True))

    @patch('backup.drivers.base.subprocess.Popen')
    def test_unpack_encrypted_restore_pipeline(self, mock_popen):
        '''Postgres restore never has a separate gunzip stage (tar -z
        decompresses inline), so an encrypted restore should be exactly
        two stages: decrypt -> tar. This only checks pipeline structure -
        which KDF flags decrypt_cmd actually uses is a separate concern.
        '''
        procs = []

        def _side_effect(*args, **kwargs):
            proc = MagicMock(name='popen-%d' % len(procs))
            proc.communicate.return_value = (b'', b'')
            proc.returncode = 0
            procs.append(proc)
            return proc

        mock_popen.side_effect = _side_effect

        runner = self._make_runner('k3y')
        runner.location = 'backup.tar.gz.enc'
        runner.checksum = 'fakechecksum'
        runner.storage = MagicMock()
        runner.storage.load.return_value = [b'chunk1', b'chunk2']

        runner.run_restore()

        self.assertEqual(2, mock_popen.call_count)
        decrypt_proc, restore_proc = procs

        decrypt_args = mock_popen.call_args_list[0][0][0]
        self.assertEqual('openssl', decrypt_args[0])

        restore_args = mock_popen.call_args_list[1][0][0]
        self.assertEqual('tar', restore_args[0])
        self.assertEqual(
            decrypt_proc.stdout, mock_popen.call_args_list[1][1]['stdin'])
        self.assertEqual(runner.process, restore_proc)

        decrypt_proc.stdin.write.assert_any_call(b'chunk1')
        decrypt_proc.stdin.write.assert_any_call(b'chunk2')


class TestPgBasebackupIncremental(unittest.TestCase):
    def setUp(self):
        self.runner_cls = importutils.import_class(
            driver_mapping['pg_basebackup_inc'])
        self.params = {
            'wal_archive_dir': './',
            'filename': '000000010000000000000006.00000168.backup',
            'parent_location': 'http://example.com/example.tar.gz',
            'parent_checksum': '63e696c5eb85550fed0a7a1a6411eb7d'
        }
        self.metadata = {
            'start-segment': '0/3000028',
            'start-wal-file': '000000010000000000000003',
            'stop-segment': '0/3000028',
            'stop-wal-file': '000000010000000000000003',
            'checkpoint-location': '0/3000098',
            'label': '000000010000000000000006.00000168.backup',
            'parent_location': self.params.get('parent_location'),
            'parent_checksum': self.params.get('parent_checksum'),
        }

    def tearDown(self):
        if os.path.exists(self.params.get('filename')):
            os.remove(self.params.get('filename'))

    def test_instance(self):
        '''Check instance'''
        # call the method
        runner = self.runner_cls(**self.params)

        # assertions
        self.assertIsNotNone(runner)

    def test_pre_backup(self):
        # prepare the test
        runner = self.runner_cls(**self.params)
        runner.pre_backup = MagicMock(return_value=None)

        # call the method
        runner.pre_backup()

        # assertions
        runner.pre_backup.assert_called_once_with()

    def test_cmd(self):
        # prepare the test
        runner = self.runner_cls(**self.params)
        wal_file_list = [
            '000000010000000000000005',
            '000000010000000000000003',
            '000000010000000000000004'
        ]
        wal_archive_dir = self.params.get('wal_archive_dir')
        cmd = (f'tar -czf - -C {wal_archive_dir} '
               f'{" ".join(wal_file_list)}')

        runner.get_wal_files = MagicMock(return_value=wal_file_list)

        # call the method
        ret = runner._cmd()

        # assertions
        self.assertEqual(ret, cmd)

    def test_get_metadata(self):
        # prepare the test
        runner = self.runner_cls(**self.params)
        runner.get_metadata = MagicMock(return_value=self.metadata)

        # call the method
        ret = runner.get_metadata()

        # assertions
        self.assertEqual(ret, self.metadata)

    def test_incremental_restore_cmd(self):
        # prepare the test
        runner = self.runner_cls(**self.params)
        cmd = 'tar xzf - -C /var/lib/postgresql/data/pgdata'

        # call the method
        ret = runner.incremental_restore_cmd()

        # assertions
        self.assertEqual(ret, cmd)

    def test_incremental_restore(self):
        # prepare the test
        runner = self.runner_cls(**self.params)
        wal_file_list = [
            '000000010000000000000005',
            '000000010000000000000003',
            '000000010000000000000004'
        ]
        runner.get_wal_files = MagicMock(return_value=wal_file_list)
        metadata = {
            'parent_location': 'https://example.com/',
            'parent_checksum': 'cc39f022c5d10f38e963062ca40c95bd',
        }
        runner.storage = MagicMock(return_value=metadata)
        command = "testcommand"
        length = 10
        runner.incremental_restore = MagicMock(return_value=length)
        runner.incremental_restore_cmd = MagicMock(return_value=command)
        runner.unpack = MagicMock(return_value=length)

        # call the method
        ret = runner.incremental_restore({
            'location': metadata['parent_location'],
            'checksum': metadata['parent_checksum']
        })

        # assertions
        self.assertEqual(ret, length)

    def test_run_restore(self):
        # prepare the test
        runner = self.runner_cls(**self.params)
        length = 10
        runner.incremental_restore = MagicMock(return_value=length)
        runner.restore_content_length = length

        # call the method
        ret = runner.run_restore()

        # assertions
        self.assertEqual(ret, length)


if __name__ == '__main__':
    unittest.main()
