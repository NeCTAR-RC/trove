# Copyright 2021 Catalyst Cloud
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
import os
from unittest import mock

from trove.common import cfg
from trove.guestagent.common import operating_system
from trove.guestagent.datastore.postgres import manager
from trove.guestagent.datastore.postgres import service
from trove.tests.unittests import trove_testtools


class TestPostgresManager(trove_testtools.TestCase):
    def setUp(self):
        super(TestPostgresManager, self).setUp()
        manager.PostgresManager._docker_client = mock.MagicMock()
        self.patch_datastore_manager('postgresql')
        self.pg_manager = manager.PostgresManager()
        m = mock.Mock()
        m.side_effect = lambda *a, **kw: m.call_count == 1
        self.first_call_true = m

    @mock.patch('trove.guestagent.common.operating_system.remove')
    @mock.patch('os.listdir')
    @mock.patch('trove.guestagent.common.operating_system.exists')
    def test_clean_wal_archives(self, mock_exists, mock_listdir, mock_remove):
        mock_exists.return_value = True
        mock_listdir.return_value = [
            '0000000100000002000000D4',
            '00000001000000000000008D',
            '0000000100000000000000A7.00000028.backup',
            '0000000100000000000000A7',
            '0000000100000002000000E7'
        ]

        # prevent infinite recursive call
        self.pg_manager._check_wal_archive_size = self.first_call_true
        self.pg_manager.app.get_config_param = mock.Mock()
        self.pg_manager.app.get_config_param.return_value = ''
        self.pg_manager.clean_wal_archives(mock.ANY)
        self.assertEqual(1, mock_remove.call_count)

        archive_path = service.WAL_ARCHIVE_DIR
        expected_calls = [
            mock.call(
                path=os.path.join(archive_path, '00000001000000000000008D'),
                force=True, recursive=False,
                as_root=True),
        ]
        self.assertEqual(expected_calls, mock_remove.call_args_list)

    @mock.patch('trove.guestagent.common.operating_system.remove')
    @mock.patch('os.listdir')
    @mock.patch('trove.guestagent.common.operating_system.exists')
    def test_clean_wal_archives_no_backups(self, mock_exists,
                                           mock_listdir,
                                           mock_remove):
        mock_exists.return_value = True
        mock_listdir.return_value = [
            '0000000100000002000000D4',
            '00000001000000000000008D',
            '0000000100000000000000A7',
            '0000000100000002000000E7'
        ]

        # prevent infinite recursive call
        self.pg_manager._check_wal_archive_size = self.first_call_true
        self.pg_manager.app.get_config_param = mock.Mock()
        self.pg_manager.app.get_config_param.return_value = ''
        self.pg_manager.clean_wal_archives(mock.ANY)

        self.assertEqual(3, mock_remove.call_count)

        archive_path = service.WAL_ARCHIVE_DIR
        expected_calls = [
            mock.call(
                path=os.path.join(archive_path, '0000000100000002000000D4'),
                force=True, recursive=False,
                as_root=True),
            mock.call(
                path=os.path.join(archive_path, '0000000100000000000000A7'),
                force=True, recursive=False,
                as_root=True),
            mock.call(
                path=os.path.join(archive_path, '00000001000000000000008D'),
                force=True, recursive=False,
                as_root=True),
        ]
        self.assertEqual(expected_calls, mock_remove.call_args_list)

    @mock.patch.object(operating_system, 'get_dir_size')
    @mock.patch.object(operating_system, 'get_filesystem_size')
    def test_check_wal_archive_size_units(
        self, mock_get_filesystem_size, mock_get_dir_size
    ):
        """Test _check_wal_archive_size with various max_wal_size formats."""
        cases = [
            ('200', True),    # interpreted as MB
            ('200MB', True),
            ('200M', True),
            ('1G', False),
            ('2GB', False),
            ('2G', False),
            ('10G', False),
            ('2T', False),
        ]

        # fake archive dir size
        mock_get_dir_size.return_value = 5 * 1024**3  # 5GB

        # fake data dir size
        mock_get_filesystem_size.return_value = 100 * 1024**3  # 100GB

        self.pg_manager.app.get_config_param = mock.Mock()

        for val, expected_result in cases:
            with self.subTest(value=val):
                # Set fake max_wal_size in config
                self.pg_manager.app.get_config_param.return_value = val

                result = self.pg_manager._check_wal_archive_size(
                    '/fake/archive', '/fake/data'
                )

                # since archive size is 5GB, only <1GB max_wal_size config
                # should trigger True
                self.assertEqual(
                    expected_result, result,
                    ('For %s should be %s', val, expected_result))

    @mock.patch.object(operating_system, 'get_dir_size')
    @mock.patch.object(operating_system, 'get_filesystem_size')
    def test_check_wal_archive_bigger_than_half(
        self, mock_get_filesystem_size, mock_get_dir_size
    ):
        # fake archive dir size
        mock_get_dir_size.return_value = 600 * 1024**2  # 600MB

        # fake data dir size
        mock_get_filesystem_size.return_value = 1024**3  # 1GB

        self.pg_manager.app.get_config_param = mock.Mock()
        # Set fake max_wal_size in config
        self.pg_manager.app.get_config_param.return_value = '1G'

        # archive size is 600MB and data volume is 1GB, check should pass
        self.assertTrue(self.pg_manager._check_wal_archive_size(
            '/fake/archive', '/fake/data'
        ))

    def test_skip_archive_cleanup_on_disable_mark_present(self):
        self.pg_manager.app.get_config_param = mock.Mock()
        self.pg_manager.app.get_config_param.return_value = (
            "echo DISABLE_TROVE_WAL_CLEANUP > /dev/null")

        with mock.patch.object(cfg, 'get_configuration_property') as m:
            self.pg_manager.clean_wal_archives(mock.ANY)
            m.assert_not_called()


class TestPostgresManagerDoPrepare(trove_testtools.TestCase):
    def setUp(self):
        super(TestPostgresManagerDoPrepare, self).setUp()
        manager.PostgresManager._docker_client = mock.MagicMock()
        self.patch_datastore_manager('postgresql')
        self.pg_manager = manager.PostgresManager()
        self.pg_manager.app = mock.MagicMock()
        self.pg_manager.perform_restore = mock.Mock()

        # Track call ordering of start_db vs perform_restore.
        self.tracker = mock.Mock()
        self.tracker.attach_mock(self.pg_manager.app.start_db, 'start_db')
        self.tracker.attach_mock(self.pg_manager.perform_restore,
                                 'perform_restore')

    def _do_prepare(self, backup_info):
        self.pg_manager.do_prepare(
            context=mock.Mock(), packages=None, databases=None,
            memory_mb=None, users=None, device_path=None,
            mount_point=None, backup_info=backup_info,
            config_contents='', root_password=None, overrides=None,
            cluster_config=None, snapshot=None, ds_version='14')

    def _backup_info(self, backup_type):
        return {
            'id': 'backup-id',
            'location': 'https://example.com/v1/AUTH_x/db/backup-id.gz.enc',
            'checksum': 'checksum',
            'type': backup_type,
            'storage_driver': 'swift',
        }

    @mock.patch('trove.guestagent.datastore.postgres.manager'
                '.operating_system')
    def test_do_prepare_legacy_pgdump(self, mock_os):
        self._do_prepare(self._backup_info('PgDump'))

        # The database is started before the logical restore, and
        # start_db runs again at the end of do_prepare.
        names = [c[0] for c in self.tracker.mock_calls]
        self.assertEqual(
            ['start_db', 'perform_restore', 'start_db'], names)

        # No recovery.signal for a logical restore.
        mock_os.execute_shell_cmd.assert_not_called()

    @mock.patch('trove.guestagent.datastore.postgres.manager'
                '.operating_system')
    def test_do_prepare_modern_restore(self, mock_os):
        self._do_prepare(self._backup_info('full'))

        # Restore happens with the database down; recovery.signal is
        # created, then a single start_db.
        names = [c[0] for c in self.tracker.mock_calls]
        self.assertEqual(['perform_restore', 'start_db'], names)

        touch_calls = [
            c for c in mock_os.execute_shell_cmd.call_args_list
            if 'recovery.signal' in c[0][0]
        ]
        self.assertEqual(1, len(touch_calls))

    @mock.patch('trove.guestagent.datastore.postgres.manager'
                '.operating_system')
    def test_do_prepare_no_backup(self, mock_os):
        self._do_prepare(None)

        names = [c[0] for c in self.tracker.mock_calls]
        self.assertEqual(['start_db'], names)
        mock_os.execute_shell_cmd.assert_not_called()
