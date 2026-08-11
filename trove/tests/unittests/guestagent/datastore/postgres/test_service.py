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

from unittest import mock

from trove.common import constants
from trove.guestagent.datastore.postgres import service
from trove.tests.unittests import trove_testtools

statement = (
    "SELECT usename FROM pg_catalog.pg_user WHERE usesuper = true"
)
username = "postgres"
port = 5432
result = [(1, "one"), (2, 'two'), (3, 'three')]


class TestPostgresConnection(trove_testtools.TestCase):
    def setUp(self):
        super(TestPostgresConnection, self).setUp()

    # execute is expected to returns nothing
    @mock.patch(
        'trove.guestagent.datastore.postgres.service.PostgresConnection')
    def test_execute(self, mock_postgres_connection):
        postgres_connection = mock.MagicMock()
        postgres_connection.execute = mock.MagicMock(return_value=None)
        mock_postgres_connection.return_value = postgres_connection

        # assertion here
        connection = service.PostgresConnection(username, port=port)
        self.assertIsNone(connection.execute(statement),
                          'postgres_connection_execute does not returns None')

    # query is expected to returns result
    @mock.patch(
        'trove.guestagent.datastore.postgres.service.PostgresConnection')
    def test_query(self, mock_postgres_connection):
        postgres_connection = mock.MagicMock()
        postgres_connection.query = mock.MagicMock(return_value=result)
        mock_postgres_connection.return_value = postgres_connection

        # assertion here
        connection = service.PostgresConnection(username, port=port)
        self.assertEqual(result, connection.query(statement),
                         'postgres_connection_query does not returns expected')


class TestIsLegacyPgdumpBackup(trove_testtools.TestCase):
    def test_legacy_pgdump(self):
        self.assertTrue(
            service.is_legacy_pgdump_backup({'type': 'PgDump'}))

    def test_modern_and_other_legacy_types(self):
        for backup_type in ('full', 'incremental', 'PgBaseBackup',
                            'PgBaseBackupIncremental', None):
            self.assertFalse(
                service.is_legacy_pgdump_backup({'type': backup_type}))

    def test_missing_type(self):
        self.assertFalse(service.is_legacy_pgdump_backup({}))

    def test_no_backup_info(self):
        self.assertFalse(service.is_legacy_pgdump_backup(None))


class TestPgSqlAppRestoreBackup(trove_testtools.TestCase):
    def setUp(self):
        super(TestPgSqlAppRestoreBackup, self).setUp()
        self.patch_datastore_manager('postgresql')
        self.app = service.PgSqlApp(mock.MagicMock(), mock.MagicMock())
        self.app.get_backup_image = mock.Mock(return_value='backup_image')
        self.app.get_backup_strategy = mock.Mock(
            return_value='pg_basebackup')
        self.app.stop_db = mock.Mock()
        self.context = mock.Mock(auth_token='token', project_id='project')

    def _backup_info(self, backup_type):
        return {
            'id': 'backup-id',
            'location': 'https://example.com/v1/AUTH_x/db/backup-id.gz.enc',
            'checksum': 'checksum',
            'type': backup_type,
            'storage_driver': 'swift',
        }

    @mock.patch('trove.guestagent.datastore.postgres.service.operating_system')
    @mock.patch('trove.guestagent.datastore.postgres.service.docker_util')
    def test_legacy_pgdump_restore(self, mock_docker, mock_os):
        mock_docker.run_container.return_value = (['restored'], True)

        self.app.restore_backup(
            self.context, self._backup_info('PgDump'), '/tmp/restore')

        args, kwargs = mock_docker.run_container.call_args
        command = kwargs['command']
        self.assertIn('--driver=pg_dump', command)
        self.assertIn('--restore-from=', command)
        self.assertNotIn('--pg-wal-archive-dir', command)

        # Only the postgres socket is mounted, read-only.
        volumes = kwargs['volumes']
        self.assertEqual(
            {constants.POSTGRESQL_HOST_SOCKET_PATH: {
                'bind': '/var/run/postgresql', 'mode': 'ro'}},
            volumes)

        # A logical restore must not stop the database or touch the
        # data directory.
        self.app.stop_db.assert_not_called()
        mock_os.remove_dir_contents.assert_not_called()
        mock_os.chown.assert_not_called()

    @mock.patch('trove.guestagent.datastore.postgres.service.LOG')
    @mock.patch('trove.guestagent.datastore.postgres.service.operating_system')
    @mock.patch('trove.guestagent.datastore.postgres.service.docker_util')
    def test_legacy_pgdump_restore_failure(self, mock_docker, mock_os,
                                           mock_log):
        mock_docker.run_container.return_value = (['boom'], False)

        self.assertRaisesRegex(
            Exception, 'Failed to run legacy pg_dump restore',
            self.app.restore_backup,
            self.context, self._backup_info('PgDump'), '/tmp/restore')

    @mock.patch('trove.guestagent.datastore.postgres.service.operating_system')
    @mock.patch('trove.guestagent.datastore.postgres.service.docker_util')
    def test_modern_restore_unchanged(self, mock_docker, mock_os):
        mock_docker.run_container.return_value = (['restored'], True)

        self.app.restore_backup(
            self.context, self._backup_info('full'), '/tmp/restore')

        args, kwargs = mock_docker.run_container.call_args
        command = kwargs['command']
        self.assertIn('--driver=pg_basebackup', command)
        self.assertIn('--pg-wal-archive-dir', command)
        self.assertIn('/var/lib/postgresql/data', kwargs['volumes'])

        self.app.stop_db.assert_called_once_with()
        self.assertTrue(mock_os.remove_dir_contents.called)
