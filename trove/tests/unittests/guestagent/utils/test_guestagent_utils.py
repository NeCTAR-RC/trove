#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json
import os
import tempfile
from unittest import mock
from unittest import TestCase

from trove.common import constants
from trove.common import exception
from trove.guestagent.common import guestagent_utils
from trove.guestagent.common.guestagent_utils import (
    prevent_major_version_upgrade,
)


class TestPreventMajorVersionUpgrade(TestCase):

    def test_minor_version_upgrade_allowed(self):
        allowed_versions = [
            ('17', '17'),
            ('17.1', '17.2'),
            ('5.7.39', '5.7.40'),
        ]

        for current, target in allowed_versions:
            prevent_major_version_upgrade(current, target)

    def test_major_version_upgrade_forbidden(self):
        forbidden_versions = [
            ('17.1', '18.1'),
            ('5.7.40', '8.0'),
        ]

        for current, target in forbidden_versions:
            self.assertRaises(
                exception.TroveError,
                prevent_major_version_upgrade,
                current,
                target
            )


class FakeNetlinkMessage(dict):
    def get_attr(self, name):
        return self.get(name)


class TestResolveEth1Config(TestCase):

    def setUp(self):
        super(TestResolveEth1Config, self).setUp()
        self.eth1_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False)
        self.addCleanup(os.unlink, self.eth1_file.name)
        patcher = mock.patch.object(
            constants, 'ETH1_CONFIG_PATH', self.eth1_file.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_config(self, config):
        with open(self.eth1_file.name, 'w') as fd:
            json.dump(config, fd)

    def _read_config(self):
        with open(self.eth1_file.name) as fd:
            return json.load(fd)

    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_resolve_marker(self, mock_iproute):
        self._write_config({'mode': 'discover',
                            'mgmt_mac': 'FA:16:3E:00:00:01'})

        ipr = mock_iproute.return_value.__enter__.return_value
        ipr.get_links.return_value = [
            FakeNetlinkMessage({'index': 1, 'IFLA_IFNAME': 'lo',
                                'IFLA_ADDRESS': '00:00:00:00:00:00'}),
            FakeNetlinkMessage({'index': 2, 'IFLA_IFNAME': 'ens3',
                                'IFLA_ADDRESS': 'fa:16:3e:00:00:01'}),
            FakeNetlinkMessage({'index': 3, 'IFLA_IFNAME': 'ens6',
                                'IFLA_ADDRESS': 'fa:16:3e:00:00:02'}),
        ]
        ipr.get_addr.return_value = [
            FakeNetlinkMessage({'prefixlen': 24,
                                'IFA_ADDRESS': '192.168.1.5'}),
            FakeNetlinkMessage({'prefixlen': 64,
                                'IFA_ADDRESS': 'fe80::f816:3eff:fe00:2'}),
        ]
        ipr.get_routes.return_value = [
            FakeNetlinkMessage({'dst_len': 24, 'RTA_OIF': 3}),
            FakeNetlinkMessage({'dst_len': 0, 'RTA_OIF': 3,
                                'RTA_GATEWAY': '192.168.1.1'}),
        ]

        guestagent_utils.resolve_eth1_config()

        self.assertEqual(
            {
                'mac_address': 'fa:16:3e:00:00:02',
                'ipv4_address': '192.168.1.5',
                'ipv4_cidr': '192.168.1.0/24',
                'ipv4_gateway': '192.168.1.1',
            },
            self._read_config())
        ipr.get_addr.assert_called_with(index=3)

    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_resolve_no_candidate_interface(self, mock_iproute):
        self._write_config({'mode': 'discover',
                            'mgmt_mac': 'fa:16:3e:00:00:01'})

        ipr = mock_iproute.return_value.__enter__.return_value
        ipr.get_links.return_value = [
            FakeNetlinkMessage({'index': 1, 'IFLA_IFNAME': 'lo',
                                'IFLA_ADDRESS': '00:00:00:00:00:00'}),
            FakeNetlinkMessage({'index': 2, 'IFLA_IFNAME': 'ens3',
                                'IFLA_ADDRESS': 'fa:16:3e:00:00:01'}),
        ]

        self.assertRaises(exception.TroveError,
                          guestagent_utils.resolve_eth1_config)

    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_resolve_without_mgmt_mac_refuses_to_guess(self, mock_iproute):
        # Picking an interface with nothing to exclude could move the
        # management interface into the database container.
        self._write_config({'mode': 'discover'})

        self.assertRaises(exception.TroveError,
                          guestagent_utils.resolve_eth1_config)
        mock_iproute.assert_not_called()

    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_link_local_ipv6_gateway_is_ignored(self, mock_iproute):
        # A link local next hop is outside the ipam pool and docker
        # rejects it when the network is created.
        self._write_config({'mode': 'discover',
                            'mgmt_mac': 'fa:16:3e:00:00:01'})

        ipr = mock_iproute.return_value.__enter__.return_value
        ipr.get_links.return_value = [
            FakeNetlinkMessage({'index': 3, 'IFLA_IFNAME': 'ens6',
                                'IFLA_ADDRESS': 'fa:16:3e:00:00:02'}),
        ]
        ipr.get_addr.return_value = [
            FakeNetlinkMessage({'prefixlen': 64,
                                'IFA_ADDRESS': '2001:db8::5'}),
        ]
        ipr.get_routes.return_value = [
            FakeNetlinkMessage({'dst_len': 0, 'RTA_OIF': 3,
                                'RTA_GATEWAY': 'fe80::1'}),
        ]

        guestagent_utils.resolve_eth1_config()

        self.assertNotIn('ipv6_gateway', self._read_config())

    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_missing_gateway_key_is_omitted(self, mock_iproute):
        self._write_config({'mode': 'discover',
                            'mgmt_mac': 'fa:16:3e:00:00:01'})

        ipr = mock_iproute.return_value.__enter__.return_value
        ipr.get_links.return_value = [
            FakeNetlinkMessage({'index': 3, 'IFLA_IFNAME': 'ens6',
                                'IFLA_ADDRESS': 'fa:16:3e:00:00:02'}),
        ]
        ipr.get_addr.return_value = [
            FakeNetlinkMessage({'prefixlen': 24,
                                'IFA_ADDRESS': '192.168.1.5'}),
        ]
        # No default route via this interface.
        ipr.get_routes.return_value = [
            FakeNetlinkMessage({'dst_len': 0, 'RTA_OIF': 2,
                                'RTA_GATEWAY': '10.0.0.1'}),
        ]

        guestagent_utils.resolve_eth1_config()

        self.assertNotIn('ipv4_gateway', self._read_config())

    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_resolved_config_untouched(self, mock_iproute):
        config = {'mac_address': 'fa:16:3e:00:00:02',
                  'ipv4_address': '10.0.0.4'}
        self._write_config(config)

        guestagent_utils.resolve_eth1_config()

        self.assertEqual(config, self._read_config())
        mock_iproute.assert_not_called()

    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_missing_file_is_noop(self, mock_iproute):
        os.unlink(self.eth1_file.name)
        # recreate for cleanup
        self.addCleanup(lambda: open(self.eth1_file.name, 'w').close())

        guestagent_utils.resolve_eth1_config()

        mock_iproute.assert_not_called()


class TestDisableUserDefinedPort(TestCase):

    def setUp(self):
        super(TestDisableUserDefinedPort, self).setUp()
        self.eth1_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False)
        self.addCleanup(os.unlink, self.eth1_file.name)
        patcher = mock.patch.object(
            constants, 'ETH1_CONFIG_PATH', self.eth1_file.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch.object(guestagent_utils, 'operating_system')
    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_unresolved_marker_does_not_touch_any_interface(
            self, mock_iproute, mock_os):
        # The marker has no mac_address. get_links(address=None) must never
        # be called: pyroute2 drops the None from the dump filter, the
        # empty filter matches every interface, and ifaces[0] is lo.
        with open(self.eth1_file.name, 'w') as fd:
            json.dump({'mode': 'discover', 'mgmt_mac': 'fa:16:3e:00:00:01'},
                      fd)

        guestagent_utils.disable_user_defined_port()

        mock_iproute.assert_not_called()
        mock_os.execute_shell_cmd.assert_not_called()

    @mock.patch.object(guestagent_utils, 'operating_system')
    @mock.patch.object(guestagent_utils, 'IPRoute')
    def test_resolved_config_downs_the_user_port(self, mock_iproute, mock_os):
        with open(self.eth1_file.name, 'w') as fd:
            json.dump({'mac_address': 'fa:16:3e:00:00:02'}, fd)
        ipr = mock_iproute.return_value.__enter__.return_value
        ipr.get_links.return_value = [
            FakeNetlinkMessage({'index': 3, 'IFLA_IFNAME': 'ens6'})]

        guestagent_utils.disable_user_defined_port()

        ipr.get_links.assert_called_once_with(address='fa:16:3e:00:00:02')
        mock_os.execute_shell_cmd.assert_called_once_with(
            'ip link set ens6 down', [], shell=True, as_root=True)
