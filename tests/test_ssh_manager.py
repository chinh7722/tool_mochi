"""Local profile validation, safe argv, host trust and private-key import."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from core.ssh_connections import SSHProfiles,validate_profile


class SSHProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=SSHProfiles(self.temp.name)
        self.key=Path(self.temp.name)/'key with spaces';self.key.write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----')
        self.profile=dict(name='Mochi VPS',host='sv50.pcrender.net',port=20052,user='root1',identity=str(self.key),local_port=8765,remote_port=8765)

    def test_save_and_reload_keeps_key_path_without_private_key_or_password_text(self):
        self.store.save([self.profile]);self.assertEqual(self.store.load(),[self.profile])
        raw=self.store.path.read_text();self.assertNotIn('PRIVATE KEY',raw);self.assertNotIn('password',raw)

    def test_ssh_arguments_bind_only_loopback_and_enforce_host_and_identity_checks(self):
        argv=self.store.ssh_arguments(self.profile)
        self.assertIn('StrictHostKeyChecking=yes',argv);self.assertIn('BatchMode=yes',argv)
        self.assertIn('ExitOnForwardFailure=yes',argv);self.assertIn('IdentitiesOnly=yes',argv)
        self.assertEqual(argv[argv.index('-L')+1],'127.0.0.1:8765:127.0.0.1:8765')
        self.assertEqual(argv[argv.index('-i')+1],str(self.key.resolve()))
        self.assertEqual(argv[-1],'root1@sv50.pcrender.net')

    def test_invalid_hostname_user_or_port_cannot_inject_ssh_options(self):
        for changes in ({'host':'-oProxyCommand=bad'},{'host':'host;bad'},{'user':'-oBad'},{'port':True},{'local_port':0}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate_profile({**self.profile,**changes})

    def test_missing_key_does_not_connect(self):
        with self.assertRaises(ValueError):self.store.ssh_arguments({**self.profile,'identity':'missing-key'})

    def test_known_hosts_option_quotes_paths_with_spaces_for_ssh_config_parser(self):
        store=SSHProfiles(Path(self.temp.name)/'folder with spaces')
        argv=store.ssh_arguments(self.profile)
        self.assertIn('UserKnownHostsFile="'+store.known_hosts.as_posix()+'"',argv)

    def test_import_private_key_copies_to_data_and_leaves_original_untouched(self):
        imported=self.store.import_key(self.key)
        self.assertTrue(imported.is_relative_to(Path(self.temp.name)/'ssh_keys'))
        self.assertEqual(imported.read_bytes(),self.key.read_bytes());self.assertNotEqual(imported,self.key)

    def test_import_public_file_is_rejected(self):
        self.key.write_text('ssh-ed25519 AAAAFake public')
        with self.assertRaises(ValueError):self.store.import_key(self.key)

    def test_host_scan_requires_exact_host_port_and_returns_public_fingerprint(self):
        raw=b'fake-public-host-key'*4;encoded=base64.b64encode(raw).decode()
        line='[sv50.pcrender.net]:20052 ssh-ed25519 '+encoded
        scanned,fingerprint=self.store.scanned_key(self.profile,'# banner\n'+line)
        self.assertEqual(scanned,line);self.assertTrue(fingerprint.startswith('SHA256:'))
        self.assertFalse(self.store.known_hosts.exists())
        with self.assertRaises(ValueError):self.store.scanned_key({**self.profile,'port':22},line)

    def test_trust_host_keeps_other_hosts_and_replaces_only_approved_host(self):
        other='other-host ssh-ed25519 '+base64.b64encode(b'other'*12).decode()
        self.store.known_hosts.write_text(other+'\n')
        line='[sv50.pcrender.net]:20052 ssh-ed25519 '+base64.b64encode(b'hostkey'*12).decode()
        self.store.trust_key(self.profile,line);self.store.trust_key(self.profile,line)
        content=self.store.known_hosts.read_text()
        self.assertIn(other,content);self.assertEqual(content.count(line),1)

    def test_setting_manager_url_keeps_existing_api_token(self):
        path=Path(self.temp.name)/'remote_connection.json';path.write_text(json.dumps({'url':'old','token':'offline-token'}))
        self.store.update_manager_url(self.profile)
        result=json.loads(path.read_text())
        self.assertEqual(result,{'url':'http://127.0.0.1:8765','token':'offline-token'})


from PySide6.QtWidgets import QApplication
from gui.ssh_manager import SSHManager

class SSHWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_window_saves_selected_identity_and_reopens_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            window=SSHManager(directory,ROOT)
            window.fields['identity'].setText(str(Path(directory)/'identity'))
            window.save();window.close()
            other=SSHManager(directory,ROOT)
            self.assertEqual(other.fields['host'].text(),'sv50.pcrender.net')
            self.assertEqual(other.fields['identity'].text(),str(Path(directory)/'identity'))
            other.close();window.deleteLater();other.deleteLater();self.app.processEvents()

if __name__=='__main__':unittest.main()
