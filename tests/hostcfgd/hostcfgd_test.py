import os
import sys
import time
import swsscommon as swsscommon_package
from sonic_py_common import device_info
from swsscommon import swsscommon

from parameterized import parameterized
from sonic_py_common.general import load_module_from_source
from unittest import TestCase, mock

from .test_vectors import HOSTCFG_DAEMON_INIT_CFG_DB, HOSTCFG_DAEMON_CFG_DB
from tests.common.mock_configdb import MockConfigDb, MockDBConnector

from pyfakefs.fake_filesystem_unittest import patchfs
from deepdiff import DeepDiff
from unittest.mock import call

test_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
modules_path = os.path.dirname(test_path)
scripts_path = os.path.join(modules_path, 'scripts')
sys.path.insert(0, modules_path)

# Load the file under test
hostcfgd_path = os.path.join(scripts_path, 'hostcfgd')
hostcfgd = load_module_from_source('hostcfgd', hostcfgd_path)
hostcfgd.ConfigDBConnector = MockConfigDb
hostcfgd.DBConnector = MockDBConnector
hostcfgd.Table = mock.Mock()


class TesNtpCfgd(TestCase):
    """
        Test hostcfd daemon - NtpCfgd
    """
    def setUp(self):
        MockConfigDb.CONFIG_DB['NTP'] = {'global': {'vrf': 'mgmt', 'src_intf': 'eth0'}}
        MockConfigDb.CONFIG_DB['NTP_SERVER'] = {'0.debian.pool.ntp.org': {}}

    def tearDown(self):
        MockConfigDb.CONFIG_DB = {}

    def test_ntp_global_update_with_no_servers(self):
        with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
            popen_mock = mock.Mock()
            attrs = {'communicate.return_value': ('output', 'error')}
            popen_mock.configure_mock(**attrs)
            mocked_subprocess.Popen.return_value = popen_mock

            ntpcfgd = hostcfgd.NtpCfg()
            ntpcfgd.ntp_global_update('global', MockConfigDb.CONFIG_DB['NTP']['global'])

            mocked_subprocess.check_call.assert_not_called()

    def test_ntp_global_update_ntp_servers(self):
        with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
            popen_mock = mock.Mock()
            attrs = {'communicate.return_value': ('output', 'error')}
            popen_mock.configure_mock(**attrs)
            mocked_subprocess.Popen.return_value = popen_mock

            ntpcfgd = hostcfgd.NtpCfg()
            ntpcfgd.ntp_global_update('global', MockConfigDb.CONFIG_DB['NTP']['global'])
            ntpcfgd.ntp_server_update('0.debian.pool.ntp.org', 'SET')
            mocked_subprocess.check_call.assert_has_calls([call(['systemctl', 'restart', 'ntp-config'])])

    def test_loopback_update(self):
        with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
            popen_mock = mock.Mock()
            attrs = {'communicate.return_value': ('output', 'error')}
            popen_mock.configure_mock(**attrs)
            mocked_subprocess.Popen.return_value = popen_mock

            ntpcfgd = hostcfgd.NtpCfg()
            ntpcfgd.ntp_global = MockConfigDb.CONFIG_DB['NTP']['global']
            ntpcfgd.ntp_servers.add('0.debian.pool.ntp.org')

            ntpcfgd.handle_ntp_source_intf_chg('eth0')
            mocked_subprocess.check_call.assert_has_calls([call(['systemctl', 'restart', 'ntp-config'])])


class TestHostcfgdDaemon(TestCase):

    def setUp(self):
        self.get_dev_meta = mock.patch(
            'sonic_py_common.device_info.get_device_runtime_metadata',
            return_value={'DEVICE_RUNTIME_METADATA': {}})
        self.get_dev_meta.start()
        MockConfigDb.set_config_db(HOSTCFG_DAEMON_CFG_DB)

    def tearDown(self):
        MockConfigDb.CONFIG_DB = {}
        self.get_dev_meta.stop()

    def test_loopback_events(self):
        MockConfigDb.set_config_db(HOSTCFG_DAEMON_CFG_DB)
        MockConfigDb.event_queue = [('NTP', 'global'),
                                  ('NTP_SERVER', '0.debian.pool.ntp.org'),
                                  ('LOOPBACK_INTERFACE', 'Loopback0|10.184.8.233/32')]
        daemon = hostcfgd.HostConfigDaemon()
        daemon.register_callbacks()
        with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
            popen_mock = mock.Mock()
            attrs = {'communicate.return_value': ('output', 'error')}
            popen_mock.configure_mock(**attrs)
            mocked_subprocess.Popen.return_value = popen_mock
            try:
                daemon.start()
            except TimeoutError:
                pass
            expected = [call(['systemctl', 'restart', 'ntp-config']),
            call(['iptables', '-t', 'mangle', '--append', 'PREROUTING', '-p', 'tcp', '--tcp-flags', 'SYN', 'SYN', '-d', '10.184.8.233', '-j', 'TCPMSS', '--set-mss', '1460']),
            call(['iptables', '-t', 'mangle', '--append', 'POSTROUTING', '-p', 'tcp', '--tcp-flags', 'SYN', 'SYN', '-s', '10.184.8.233', '-j', 'TCPMSS', '--set-mss', '1460'])]
            mocked_subprocess.check_call.assert_has_calls(expected, any_order=True)

    def test_kdump_event(self):
        MockConfigDb.set_config_db(HOSTCFG_DAEMON_CFG_DB)
        daemon = hostcfgd.HostConfigDaemon()
        daemon.register_callbacks()
        MockConfigDb.event_queue = [('KDUMP', 'config')]
        with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
            popen_mock = mock.Mock()
            attrs = {'communicate.return_value': ('output', 'error')}
            popen_mock.configure_mock(**attrs)
            mocked_subprocess.Popen.return_value = popen_mock
            try:
                daemon.start()
            except TimeoutError:
                pass
            expected = [call(['sonic-kdump-config', '--disable']),
                        call(['sonic-kdump-config', '--num_dumps', '3']),
                        call(['sonic-kdump-config', '--memory', '0M-2G:256M,2G-4G:320M,4G-8G:384M,8G-:448M'])]
            mocked_subprocess.check_call.assert_has_calls(expected, any_order=True)

    def test_devicemeta_event(self):
        """
        Test handling DEVICE_METADATA events.
        1) Hostname reload
        1) Timezone reload
        """
        MockConfigDb.set_config_db(HOSTCFG_DAEMON_CFG_DB)
        MockConfigDb.event_queue = [(swsscommon.CFG_DEVICE_METADATA_TABLE_NAME,
                                    'localhost')]
        daemon = hostcfgd.HostConfigDaemon()
        daemon.aaacfg = mock.MagicMock()
        daemon.iptables = mock.MagicMock()
        daemon.passwcfg = mock.MagicMock()
        daemon.dnscfg = mock.MagicMock()
        daemon.load(HOSTCFG_DAEMON_INIT_CFG_DB)
        daemon.register_callbacks()
        with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
            try:
                daemon.start()
            except TimeoutError:
                pass

            expected = [
                call(['sudo', 'service', 'hostname-config', 'restart']),
                call(['sudo', 'monit', 'reload']),
                call(['timedatectl', 'set-timezone', 'Europe/Kyiv']),
                call(['systemctl', 'restart', 'rsyslog']),
            ]
            mocked_subprocess.check_call.assert_has_calls(expected,
                                                          any_order=True)

        # Mock empty name
        HOSTCFG_DAEMON_CFG_DB["DEVICE_METADATA"]["localhost"]["hostname"] = ""
        original_syslog = hostcfgd.syslog
        MockConfigDb.set_config_db(HOSTCFG_DAEMON_CFG_DB)
        with mock.patch('hostcfgd.syslog') as mocked_syslog:
            with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
                mocked_syslog.LOG_ERR = original_syslog.LOG_ERR
                try:
                    daemon.start()
                except TimeoutError:
                    pass

                expected = [
                    call(original_syslog.LOG_ERR, 'Hostname was not updated: Empty not allowed')
                ]
                mocked_syslog.syslog.assert_has_calls(expected)

        daemon.devmetacfg.hostname = "SameHostName"
        HOSTCFG_DAEMON_CFG_DB["DEVICE_METADATA"]["localhost"]["hostname"] = daemon.devmetacfg.hostname
        MockConfigDb.set_config_db(HOSTCFG_DAEMON_CFG_DB)
        with mock.patch('hostcfgd.syslog') as mocked_syslog:
            with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
                mocked_syslog.LOG_INFO = original_syslog.LOG_INFO
                try:
                    daemon.start()
                except TimeoutError:
                    pass

                expected = [
                    call(original_syslog.LOG_INFO, 'Hostname was not updated: Already set up with the same name: SameHostName')
                ]
                mocked_syslog.syslog.assert_has_calls(expected)

    def test_mgmtiface_event(self):
        """
        Test handling mgmt events.
        1) Management interface setup
        2) Management vrf setup
        """
        MockConfigDb.set_config_db(HOSTCFG_DAEMON_CFG_DB)
        MockConfigDb.event_queue = [
            (swsscommon.CFG_MGMT_INTERFACE_TABLE_NAME, 'eth0|1.2.3.4/24'),
            (swsscommon.CFG_MGMT_VRF_CONFIG_TABLE_NAME, 'vrf_global')
        ]
        daemon = hostcfgd.HostConfigDaemon()
        daemon.register_callbacks()
        daemon.aaacfg = mock.MagicMock()
        daemon.iptables = mock.MagicMock()
        daemon.passwcfg = mock.MagicMock()
        daemon.dnscfg = mock.MagicMock()
        daemon.load(HOSTCFG_DAEMON_INIT_CFG_DB)
        with mock.patch('hostcfgd.check_output_pipe') as mocked_check_output:
            with mock.patch('hostcfgd.subprocess') as mocked_subprocess:
                popen_mock = mock.Mock()
                attrs = {'communicate.return_value': ('output', 'error')}
                popen_mock.configure_mock(**attrs)
                mocked_subprocess.Popen.return_value = popen_mock

                try:
                    daemon.start()
                except TimeoutError:
                    pass

                expected = [
                    call(['sudo', 'systemctl', 'restart', 'interfaces-config']),
                    call(['sudo', 'systemctl', 'restart', 'ntp-config']),
                    call(['service', 'ntp', 'stop']),
                    call(['systemctl', 'restart', 'interfaces-config']),
                    call(['service', 'ntp', 'start']),
                    call(['ip', '-4', 'route', 'del', 'default', 'dev', 'eth0', 'metric', '202'])
                ]
                mocked_subprocess.check_call.assert_has_calls(expected)
                expected = [
                    call(['cat', '/proc/net/route'], ['grep', '-E', r"eth0\s+00000000\s+[0-9A-Z]+\s+[0-9]+\s+[0-9]+\s+[0-9]+\s+202"], ['wc', '-l'])
                ]
                mocked_check_output.assert_has_calls(expected)

    def test_dns_events(self):
        MockConfigDb.set_config_db(HOSTCFG_DAEMON_CFG_DB)
        MockConfigDb.event_queue = [('DNS_NAMESERVER', '1.1.1.1')]
        daemon = hostcfgd.HostConfigDaemon()
        daemon.register_callbacks()
        with mock.patch('hostcfgd.run_cmd') as mocked_run_cmd:
            try:
                daemon.start()
            except TimeoutError:
                pass
            mocked_run_cmd.assert_has_calls([call(['systemctl', 'restart', 'resolv-config'], True, False)])


class TestDnsHandler:

    @mock.patch('hostcfgd.run_cmd')
    def test_dns_update(self, mock_run_cmd):
        dns_cfg = hostcfgd.DnsCfg()
        key = "1.1.1.1"
        dns_cfg.dns_update(key, {})

        mock_run_cmd.assert_has_calls([call(['systemctl', 'restart', 'resolv-config'], True, False)])

    def test_load(self):
        dns_cfg = hostcfgd.DnsCfg()
        dns_cfg.dns_update = mock.MagicMock()

        data = {}
        dns_cfg.load(data)
        dns_cfg.dns_update.assert_called()
