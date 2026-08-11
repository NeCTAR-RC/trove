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


import unittest
from unittest.mock import MagicMock, patch

from oslo_config import cfg
from oslo_utils import importutils

CONF = cfg.CONF
CONF.backup_encryption_key = None

DRIVER_CLASS = 'backup.drivers.pg_dump.PgDump'


class TestPgDump(unittest.TestCase):
    """Restore-only driver for legacy (Ussuri-era) pg_dumpall backups."""

    def setUp(self):
        self.runner_cls = importutils.import_class(DRIVER_CLASS)
        # The kwargs stream_restore_from_storage() in main.py passes.
        self.params = {
            'storage': MagicMock(),
            'location': 'https://example.com/v1/AUTH_x/db/backup_id.gz.enc',
            'checksum': '63e696c5eb85550fed0a7a1a6411eb7d',
            'wal_archive_dir': None,
            'lsn': None,
        }

    def _make_runner(self, encrypt_key='k3y', **overrides):
        params = dict(self.params, **overrides)
        with patch.object(self.runner_cls, 'encrypt_key', encrypt_key):
            runner = self.runner_cls(**params)
        runner.encrypt_key = encrypt_key
        return runner

    def test_gzip_enabled_after_init(self):
        '''Regression: BaseRunner.__init__ resets _gzip to False; without
        the flag the gunzip stage is skipped and psql receives gzip bytes.
        '''
        runner = self._make_runner()

        self.assertTrue(runner._gzip)

    def test_restore_command(self):
        runner = self._make_runner()

        self.assertEqual('psql -U postgres', runner.restore_command)

    def test_tolerates_missing_wal_archive_dir(self):
        params = dict(self.params)
        del params['wal_archive_dir']
        with patch.object(self.runner_cls, 'encrypt_key', 'k3y'):
            runner = self.runner_cls(**params)

        self.assertIsNotNone(runner)

    def test_unencrypted_location_without_key(self):
        runner = self._make_runner(
            encrypt_key=None,
            location='https://example.com/v1/AUTH_x/db/backup_id.gz')

        self.assertEqual('', runner.decrypt_cmd)

    def test_decrypt_cmd_uses_pbkdf2(self):
        '''Legacy postgres backups were encrypted with -pbkdf2 (no
        -md/-iter), unlike mysql/mariadb - the decrypt command must match.
        '''
        runner = self._make_runner()

        self.assertIn('-pbkdf2', runner.decrypt_cmd.split())
        self.assertNotIn('-md', runner.decrypt_cmd.split())
        self.assertNotIn('-iter', runner.decrypt_cmd.split())
        self.assertIn('pass:k3y', runner.decrypt_cmd.split())

    def test_backup_not_supported(self):
        runner = self._make_runner()

        self.assertRaisesRegex(
            Exception, 'not supported', runner.pre_backup)
        # The context manager protocol (used by the backup path) calls
        # pre_backup() first, so backup use fails up front.
        self.assertRaisesRegex(
            Exception, 'not supported', runner.__enter__)

    def test_check_restore_process(self):
        runner = self._make_runner()
        runner.process = MagicMock()

        runner.process.returncode = 0
        self.assertTrue(runner.check_restore_process())

        runner.process.returncode = 1
        self.assertFalse(runner.check_restore_process())

    @patch('backup.drivers.base.subprocess.Popen')
    def test_unpack_encrypted_gzipped_pipeline(self, mock_popen):
        '''A legacy <backup_id>.gz.enc object must be restored through
        exactly three stages: openssl decrypt -> gunzip -> psql, each
        stage's stdout feeding the next one's stdin.
        '''
        procs = []

        def _side_effect(*args, **kwargs):
            proc = MagicMock(name='popen-%d' % len(procs))
            proc.communicate.return_value = (b'', b'')
            proc.returncode = 0
            procs.append(proc)
            return proc

        mock_popen.side_effect = _side_effect

        runner = self._make_runner()
        runner.storage.load.return_value = [b'chunk1', b'chunk2']

        runner.run_restore()

        self.assertEqual(3, mock_popen.call_count)
        decrypt_proc, gunzip_proc, restore_proc = procs

        decrypt_args = mock_popen.call_args_list[0][0][0]
        self.assertEqual('openssl', decrypt_args[0])

        gunzip_args = mock_popen.call_args_list[1][0][0]
        self.assertEqual(['gzip', '-d', '-c'], gunzip_args)
        self.assertEqual(
            decrypt_proc.stdout, mock_popen.call_args_list[1][1]['stdin'])

        restore_args = mock_popen.call_args_list[2][0][0]
        self.assertEqual(['psql', '-U', 'postgres'], restore_args)
        self.assertEqual(
            gunzip_proc.stdout, mock_popen.call_args_list[2][1]['stdin'])
        self.assertEqual(runner.process, restore_proc)

        decrypt_proc.stdin.write.assert_any_call(b'chunk1')
        decrypt_proc.stdin.write.assert_any_call(b'chunk2')


if __name__ == '__main__':
    unittest.main()
