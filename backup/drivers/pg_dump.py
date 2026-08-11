# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo_log import log as logging

from backup.drivers import base

LOG = logging.getLogger(__name__)


class PgDump(base.BaseRunner):
    """Restore-only driver for legacy (pre-Victoria) pg_dumpall backups.

    Ussuri-era trove created postgres backups with the in-guestagent
    PgDump strategy: 'pg_dumpall | gzip | openssl enc', stored as
    <backup_id>.gz.enc. unpack() derives the decrypt and gunzip pipeline
    stages from the object name; this driver only provides the final psql
    consumer. It requires a running database reachable over the
    /var/run/postgresql unix socket (the guestagent mounts it into this
    container and starts a freshly-initialized database first).

    Creating new backups with this driver is not supported; pg_basebackup
    is the only postgres backup strategy.
    """
    restore_cmd = 'psql -U postgres'

    def __init__(self, *args, **kwargs):
        self.backup_log = '/tmp/pgdump.log'
        # BaseRunner.__init__ reads self.datadir when no restore_location
        # is passed; unused by psql but must exist.
        self.datadir = kwargs.pop(
            'db_datadir', '/var/lib/postgresql/data/pgdata')
        # main.py passes wal_archive_dir on restore whenever
        # --pg-wal-archive-dir is set; a logical restore has no use for it.
        kwargs.pop('wal_archive_dir', None)

        super(PgDump, self).__init__(*args, **kwargs)

        # Must be set after super().__init__(), which resets _gzip to
        # False. The legacy stream is gzipped SQL, so the gunzip stage in
        # unpack() must run ahead of psql.
        self._gzip = True

    def pre_backup(self):
        raise Exception('Creating PgDump backups is not supported; '
                        'pg_basebackup is the only postgres backup strategy.')

    @property
    def decrypt_cmd(self):
        """Decryption command.

        Overrides BaseRunner.decrypt_cmd (tuned for pre-Victoria
        mysql/mariadb backups, legacy KDF): Ussuri-era postgres backups,
        including PgDump ones, were encrypted with -pbkdf2 and no
        -md/-iter. See the rationale on PgBasebackup.decrypt_cmd in
        backup/drivers/postgres.py.
        """
        if self.encrypt_key:
            return ('openssl enc -d -aes-256-cbc -pbkdf2 -salt -pass pass:%s'
                    % self.encrypt_key)
        else:
            return ''

    def check_restore_process(self):
        # psql without ON_ERROR_STOP exits 0 despite benign errors like
        # 'role "postgres" already exists', matching the tolerant
        # behaviour of the Ussuri-era restore strategy.
        LOG.info('Checking return code of psql restore process.')
        return_code = self.process.returncode
        if return_code != 0:
            LOG.error('psql process exited with %s', return_code)
            return False
        return True
