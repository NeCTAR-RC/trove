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
CONF.db_user = "db_user"
CONF.db_password = "db_password"
CONF.db_host = "db_host"

# XtraBackup sets self._gzip = True and has a real restore_cmd, so it's a
# convenient concrete BaseRunner subclass to exercise unpack() through.
XtraBackup = importutils.import_class('backup.drivers.xtrabackup.XtraBackup')


class TestUnpack(unittest.TestCase):
    """Covers the decrypt/gunzip/restore-command pipeline in unpack().

    Regression coverage for a bug where an encrypted backup's decrypt
    command was computed but then immediately discarded, so restoring
    an encrypted backup fed raw encrypted bytes straight into gzip (or
    the restore command itself).
    """

    def _make_runner(self, location, encrypt_key=None):
        with patch.object(XtraBackup, 'encrypt_key', encrypt_key):
            runner = XtraBackup(
                db_datadir='/var/lib/mysql/data',
                storage=MagicMock(),
                location=location,
                checksum='fakechecksum')
        runner.encrypt_key = encrypt_key
        runner.storage.load.return_value = [b'chunk1', b'chunk2']
        return runner

    @staticmethod
    def _popen_side_effect():
        """Return distinct MagicMocks per Popen() call, each usable as
        the stdin of the next.
        """
        procs = []

        def _side_effect(*args, **kwargs):
            proc = MagicMock(name='popen-%d' % len(procs))
            proc.communicate.return_value = (b'', b'')
            proc.returncode = 0
            procs.append(proc)
            return proc

        return _side_effect, procs

    @patch('backup.drivers.base.subprocess.Popen')
    def test_unpack_plain(self, mock_popen):
        """No encryption, no gzip: chunks go straight to the restore cmd."""
        side_effect, procs = self._popen_side_effect()
        mock_popen.side_effect = side_effect

        runner = self._make_runner('backup.xbstream')
        runner.run_restore()

        self.assertEqual(1, mock_popen.call_count)
        restore_proc = procs[0]
        self.assertEqual(runner.process, restore_proc)
        restore_proc.stdin.write.assert_any_call(b'chunk1')
        restore_proc.stdin.write.assert_any_call(b'chunk2')
        restore_proc.communicate.assert_called_once()

    @patch('backup.drivers.base.subprocess.Popen')
    def test_unpack_gzip_only(self, mock_popen):
        """Gzipped, not encrypted: gunzip stage feeds the restore cmd."""
        side_effect, procs = self._popen_side_effect()
        mock_popen.side_effect = side_effect

        runner = self._make_runner('backup.xbstream.gz')
        runner.run_restore()

        self.assertEqual(2, mock_popen.call_count)
        gunzip_proc, restore_proc = procs
        gunzip_args = mock_popen.call_args_list[0][0][0]
        self.assertEqual(['gzip', '-d', '-c'], gunzip_args)
        self.assertEqual(runner.process, restore_proc)
        # restore command's stdin must be gunzip's stdout, not raw input
        self.assertEqual(
            gunzip_proc.stdout, mock_popen.call_args_list[1][1]['stdin'])
        gunzip_proc.stdin.write.assert_any_call(b'chunk1')
        gunzip_proc.stdin.write.assert_any_call(b'chunk2')

    @patch('backup.drivers.base.subprocess.Popen')
    def test_unpack_encrypted_only(self, mock_popen):
        """Encrypted, not gzipped: decrypt stage feeds the restore cmd
        directly (this path was previously entirely unfiltered).
        """
        side_effect, procs = self._popen_side_effect()
        mock_popen.side_effect = side_effect

        runner = self._make_runner('backup.xbstream.enc', encrypt_key='k3y')
        runner.run_restore()

        self.assertEqual(2, mock_popen.call_count)
        decrypt_proc, restore_proc = procs
        decrypt_args = mock_popen.call_args_list[0][0][0]
        self.assertEqual('openssl', decrypt_args[0])
        self.assertIn('pass:k3y', decrypt_args)
        self.assertEqual(runner.process, restore_proc)
        self.assertEqual(
            decrypt_proc.stdout, mock_popen.call_args_list[1][1]['stdin'])
        decrypt_proc.stdin.write.assert_any_call(b'chunk1')
        decrypt_proc.stdin.write.assert_any_call(b'chunk2')

    @patch('backup.drivers.base.subprocess.Popen')
    def test_unpack_encrypted_and_gzipped(self, mock_popen):
        """Encrypted + gzipped (the legacy Ussuri-era case): raw bytes ->
        decrypt -> gunzip -> restore command, in that order.
        """
        side_effect, procs = self._popen_side_effect()
        mock_popen.side_effect = side_effect

        runner = self._make_runner(
            'backup.xbstream.gz.enc', encrypt_key='k3y')
        runner.run_restore()

        self.assertEqual(3, mock_popen.call_count)
        decrypt_proc, gunzip_proc, restore_proc = procs

        decrypt_args = mock_popen.call_args_list[0][0][0]
        self.assertEqual('openssl', decrypt_args[0])

        gunzip_args = mock_popen.call_args_list[1][0][0]
        self.assertEqual(['gzip', '-d', '-c'], gunzip_args)
        # gunzip must read from decrypt's stdout, not raw input
        self.assertEqual(
            decrypt_proc.stdout, mock_popen.call_args_list[1][1]['stdin'])

        # restore command must read from gunzip's stdout
        self.assertEqual(
            gunzip_proc.stdout, mock_popen.call_args_list[2][1]['stdin'])
        self.assertEqual(runner.process, restore_proc)

        # raw stream chunks go to the *first* stage (decrypt), not gunzip
        decrypt_proc.stdin.write.assert_any_call(b'chunk1')
        decrypt_proc.stdin.write.assert_any_call(b'chunk2')
        gunzip_proc.stdin.write.assert_not_called()

    def test_missing_encryption_key_rejected_early(self):
        """Constructing a runner for an .enc backup with no key configured
        should fail fast, before any restore is attempted.
        """
        with self.assertRaisesRegex(
                Exception, 'Encryption key not provided'):
            self._make_runner('backup.xbstream.enc', encrypt_key=None)
