# Copyright 2015 Tesora Inc.
# All Rights Reserved.
#
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

from collections import abc
import ipaddress
import json
import os
import re
import socket

from oslo_log import log as logging
from pyroute2 import IPRoute
from semantic_version import Version

from trove.common import cfg
from trove.common import constants
from trove.common import exception
from trove.common import pagination
from trove.common import utils
from trove.guestagent.common import operating_system

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


def update_dict(updates, target):
    """Recursively update a target dictionary with given updates.

    Updates are provided as a dictionary of key-value pairs
    where a value can also be a nested dictionary in which case
    its key is treated as a sub-section of the outer key.
    If a list value is encountered the update is applied
    iteratively on all its items.

    :returns:    Will always return a dictionary of results (may be empty).
    """
    if target is None:
        target = {}

    if isinstance(target, list):
        for index, item in enumerate(target):
            target[index] = update_dict(updates, item)
        return target

    if updates is not None:
        for k, v in updates.items():
            if isinstance(v, abc.Mapping):
                target[k] = update_dict(v, target.get(k, {}))
            else:
                target[k] = updates[k]

    return target


def expand_dict(target, namespace_sep='.'):
    """Expand a flat dict to a nested one.
    This is an inverse of 'flatten_dict'.

    :seealso: flatten_dict
    """
    nested = {}
    for k, v in target.items():
        sub = nested
        keys = k.split(namespace_sep)
        for key in keys[:-1]:
            sub = sub.setdefault(key, {})
        sub[keys[-1]] = v

    return nested


def flatten_dict(target, namespace_sep='.'):
    """Flatten a nested dict.
    Return a one-level dict with all sub-level keys joined by a namespace
    separator.

    The following nested dict:
    {'ns1': {'ns2a': {'ns3a': True, 'ns3b': False}, 'ns2b': 10}}

    would be flattened to:
    {'ns1.ns2a.ns3a': True, 'ns1.ns2a.ns3b': False, 'ns1.ns2b': 10}
    """
    def flatten(target, keys, namespace_sep):
        flattened = {}
        if isinstance(target, abc.Mapping):
            for k, v in target.items():
                flattened.update(
                    flatten(v, keys + [k], namespace_sep))
        else:
            ns = namespace_sep.join(keys)
            flattened[ns] = target

        return flattened

    return flatten(target, [], namespace_sep)


def build_file_path(base_dir, base_name, *extensions):
    """Build a path to a file in a given directory.
    The file may have an extension(s).

    :returns:    Path such as: 'base_dir/base_name.ext1.ext2.ext3'
    """
    file_name = os.extsep.join([base_name] + list(extensions))
    return os.path.expanduser(os.path.join(base_dir, file_name))


def to_bytes(value):
    """Convert numbers with a byte suffix to bytes.
    """
    if isinstance(value, str):
        pattern = re.compile(r'^(\d+)([K,M,G,T]{1})$')
        match = pattern.match(value)
        if match:
            value = match.group(1)
            suffix = match.group(2)
            factor = {
                'K': 1024,
                'M': 1024 ** 2,
                'G': 1024 ** 3,
                'T': 1024 ** 4
            }[suffix]

            return int(round(factor * float(value)))

    return value


def paginate_list(li, limit=None, marker=None, include_marker=False):
    """Paginate a list of objects based on the name attribute.
    :returns:           Page sublist and a marker (name of the last item).
    """
    return pagination.paginate_object_list(
        li, 'name', limit=limit, marker=marker, include_marker=include_marker)


def serialize_list(li, limit=None, marker=None, include_marker=False):
    """
    Paginate (by name) and serialize a given object list.
    :returns:           A serialized and paginated version of a given list.
    """
    page, next_name = paginate_list(li, limit=limit, marker=marker,
                                    include_marker=include_marker)
    return [item.serialize() for item in page], next_name


def get_filesystem_volume_stats(fs_path):
    try:
        stats = os.statvfs(fs_path)
    except OSError:
        raise RuntimeError("Filesystem not found (%s)" % fs_path)

    total = stats.f_blocks * stats.f_bsize
    free = stats.f_bfree * stats.f_bsize
    # return the size in GB
    used_gb = utils.to_gb(total - free)
    total_gb = utils.to_gb(total)

    output = {
        'block_size': stats.f_bsize,
        'total_blocks': stats.f_blocks,
        'free_blocks': stats.f_bfree,
        'total': total_gb,
        'free': free,
        'used': used_gb
    }
    return output


def get_conf_dir():
    """Get the config directory for the database related settings.

    For now, the files inside the config dir are mainly for instance rebuild.
    """
    mount_point = CONF.get(CONF.datastore_manager).mount_point
    conf_dir = os.path.join(mount_point, 'conf.d')
    if not operating_system.exists(conf_dir, is_directory=True, as_root=True):
        operating_system.ensure_directory(conf_dir, as_root=True)

    return conf_dir


def _get_default_gateway(ipr, ifindex, family):
    for route in ipr.get_routes(family=family):
        if route['dst_len'] != 0:
            continue
        if route.get_attr('RTA_OIF') != ifindex:
            continue
        gateway = route.get_attr('RTA_GATEWAY')
        if gateway:
            return gateway
    return None


def _discover_database_nic(mgmt_mac):
    """Build the eth1 config from the live network state of the guest.

    The database NIC is the interface that is not the management port
    (identified by MAC address), excluding loopback and container
    interfaces. Used for instances on the Nectar default network, where
    the port is created by Nova at scheduling time and its details cannot
    be injected by the control plane before boot.
    """
    excluded_prefixes = ('lo', 'docker', 'veth', 'br-')
    with IPRoute() as ipr:
        for link in ipr.get_links():
            ifname = link.get_attr('IFLA_IFNAME') or ''
            mac = (link.get_attr('IFLA_ADDRESS') or '').lower()
            if not mac or ifname.startswith(excluded_prefixes):
                continue
            if mac == mgmt_mac:
                continue

            ifindex = link['index']
            v4_iface = None
            v6_iface = None
            for addr in ipr.get_addr(index=ifindex):
                address = addr.get_attr('IFA_ADDRESS')
                if not address:
                    continue
                iface = ipaddress.ip_interface(
                    f"{address}/{addr['prefixlen']}")
                if iface.is_link_local:
                    continue
                if iface.version == 4 and not v4_iface:
                    v4_iface = iface
                elif iface.version == 6 and not v6_iface:
                    v6_iface = iface

            if not v4_iface and not v6_iface:
                LOG.debug("Skipping interface %s (%s): no usable address",
                          ifname, mac)
                continue

            nic_info = {"mac_address": mac}
            if v4_iface:
                nic_info["ipv4_address"] = str(v4_iface.ip)
                nic_info["ipv4_cidr"] = str(v4_iface.network)
                _set_gateway(nic_info, "ipv4_gateway", ipr, ifindex,
                             socket.AF_INET)
            if v6_iface:
                nic_info["ipv6_address"] = str(v6_iface.ip)
                nic_info["ipv6_cidr"] = str(v6_iface.network)
                # Only a routable gateway is usable here. The control
                # plane deliberately leaves ipv6_gateway unset unless the
                # subnet is dhcpv6-stateful, and a link local next hop
                # from the default route is outside the ipam pool, which
                # docker rejects when the network is created.
                _set_gateway(nic_info, "ipv6_gateway", ipr, ifindex,
                             socket.AF_INET6, skip_link_local=True)

            LOG.info("Discovered database NIC %s: %s", ifname, nic_info)
            return nic_info

    raise exception.TroveError(
        "Could not discover the database network interface (management "
        "MAC: %s)" % mgmt_mac)


def _set_gateway(nic_info, key, ipr, ifindex, family,
                 skip_link_local=False):
    """Record the default gateway of an interface, when it is usable.

    The key is left out entirely rather than set to None, so that the
    config looks the same as one built by the control plane from a subnet
    without a gateway.
    """
    gateway = _get_default_gateway(ipr, ifindex, family)
    if not gateway:
        LOG.warning("No default gateway found for interface %s (%s), the "
                    "database container will have no default route",
                    ifindex, key)
        return
    if skip_link_local and ipaddress.ip_address(gateway).is_link_local:
        LOG.info("Ignoring link local %s %s for interface %s",
                 key, gateway, ifindex)
        return
    nic_info[key] = gateway


def resolve_eth1_config():
    """Resolve a deferred eth1 config left by the control plane.

    For instances on the Nectar default network the taskmanager injects a
    marker ({"mode": "discover", "mgmt_mac": ...}) instead of the real
    config, because the database port is only created by Nova at
    scheduling time.
    Rewrite the file with the discovered interface details so all readers
    (docker network setup, replication strategies) see a normal config.
    Idempotent and a no-op when the file is absent or already resolved.
    """
    if not os.path.exists(constants.ETH1_CONFIG_PATH):
        return

    with open(constants.ETH1_CONFIG_PATH) as fd:
        eth1_config = json.load(fd)
    if eth1_config.get('mode') != 'discover':
        return

    mgmt_mac = (eth1_config.get('mgmt_mac') or '').lower()
    if not mgmt_mac:
        # The management mac is the only thing telling the two interfaces
        # apart. Guessing would risk moving the management interface into
        # the database container, cutting the guest agent off the control
        # plane and exposing the database on the management network.
        raise exception.TroveError(
            "The eth1 config marker has no management mac address, refusing "
            "to guess the database network interface")
    nic_info = _discover_database_nic(mgmt_mac)
    with open(constants.ETH1_CONFIG_PATH, 'w') as fd:
        json.dump(nic_info, fd)
    LOG.info("Resolved eth1 config: %s", nic_info)


def disable_user_defined_port():
    with open(constants.ETH1_CONFIG_PATH) as fd:
        eth1_config = json.load(fd)
    mac_address = eth1_config.get("mac_address")
    if not mac_address:
        # Never call get_links() without a mac. pyroute2 drops the None
        # from the dump filter, the empty filter then matches every
        # interface, and the first one is the loopback: the port we would
        # take down is lo.
        LOG.warning("No mac address in %s, not disabling the user defined "
                    "port", constants.ETH1_CONFIG_PATH)
        return
    with IPRoute() as ipr:
        ifaces = ipr.get_links(address=mac_address)
        if not ifaces:
            return
        ifname = ifaces[0].get_attr('IFLA_IFNAME')
    operating_system.execute_shell_cmd(f"ip link set {ifname} down", [],
                                       shell=True,
                                       as_root=True)


# This helper method allows upgrade only between minor versions e.g. from 16.10
# to 16.11. Attempt to upgrade from 16.10 to 17.1  will throw a TroveError.
def prevent_major_version_upgrade(cur_ver, new_ver):
    current = Version.coerce(str(cur_ver))
    target = Version.coerce(str(new_ver))

    if current.major != target.major:
        raise exception.TroveError(
            "Major version upgrade is not allowed: "
            "%s -> %s" % (cur_ver, new_ver)
        )
