import socket
import unittest
from unittest.mock import MagicMock, patch

from host.server import _detect_lan_address


class AutoHostTests(unittest.TestCase):
    def select(self, address):
        probe = MagicMock()
        probe.__enter__.return_value.getsockname.return_value = (address, 40000)
        with patch('host.server.socket.socket', return_value=probe) as create:
            result = _detect_lan_address()
        create.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        return result

    def test_selects_current_private_address(self):
        self.assertEqual(self.select('192.168.7.32'), '192.168.7.32')
        self.assertEqual(self.select('10.23.9.4'), '10.23.9.4')

    def test_rejects_public_loopback_and_link_local(self):
        for address in ('8.8.8.8', '127.0.0.1', '169.254.1.2'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.select(address)

    def test_route_error_is_not_replaced_with_a_guessed_address(self):
        with patch('host.server.socket.socket', side_effect=OSError('no route')):
            with self.assertRaises(OSError):
                _detect_lan_address()
