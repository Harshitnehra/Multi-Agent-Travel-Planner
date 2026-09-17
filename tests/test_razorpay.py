import hashlib
import hmac
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from config.settings import Settings
from providers import RazorpayTestError, RazorpayTestGateway


class RazorpayGatewayTests(unittest.TestCase):
    def test_live_credentials_are_rejected(self):
        gateway = RazorpayTestGateway(
            Settings(razorpay_key_id="rzp_live_forbidden", razorpay_key_secret="secret")
        )
        with self.assertRaisesRegex(RazorpayTestError, "test-mode"):
            gateway.credentials()

    @patch("providers.razorpay_test.requests.post")
    def test_order_is_created_in_subunits_without_returning_secret(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"id": "order_test_provider123"}
        post.return_value = response
        gateway = RazorpayTestGateway(
            Settings(
                razorpay_key_id="rzp_test_public",
                razorpay_key_secret="server-secret",
            )
        )
        result = gateway.create_order(
            amount=Decimal("123.45"),
            currency="INR",
            receipt="demo-receipt",
            notes={"demo": "true"},
        )
        self.assertEqual(result["amount"], 12345)
        self.assertEqual(result["key_id"], "rzp_test_public")
        self.assertNotIn("secret", result)
        self.assertEqual(post.call_args.kwargs["auth"], ("rzp_test_public", "server-secret"))

    def test_signature_uses_server_order_id(self):
        secret = "server-secret"
        gateway = RazorpayTestGateway(
            Settings(razorpay_key_id="rzp_test_public", razorpay_key_secret=secret)
        )
        signature = hmac.new(
            secret.encode(), b"order_123|pay_123", hashlib.sha256
        ).hexdigest()
        self.assertTrue(gateway.verify_signature("order_123", "pay_123", signature))
        self.assertFalse(gateway.verify_signature("order_other", "pay_123", signature))


if __name__ == "__main__":
    unittest.main()
