import ssl
import unittest
from unittest.mock import patch
from radioplanner.tls import verified_context, secure_urlopen

class TlsTests(unittest.TestCase):
    def test_verification_enabled(self):
        ctx = verified_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_urlopen_receives_verified_context(self):
        with patch('radioplanner.tls.urlopen') as opener:
            secure_urlopen('https://example.com', timeout=7)
            kwargs=opener.call_args.kwargs
            self.assertEqual(kwargs['timeout'],7)
            self.assertEqual(kwargs['context'].verify_mode,ssl.CERT_REQUIRED)

if __name__ == '__main__': unittest.main()
