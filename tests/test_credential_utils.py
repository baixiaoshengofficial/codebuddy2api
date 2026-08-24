import base64
import json
import unittest

from src.credential_utils import (
    build_credential_filename,
    decode_jwt_claims,
    first_claim,
    select_claims,
)


def _unsigned_jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class CredentialUtilsTests(unittest.TestCase):
    def test_decodes_claims_and_selects_identity(self):
        claims = decode_jwt_claims(_unsigned_jwt({"email": "me@example.com", "sub": "user-1"}))

        self.assertEqual(first_claim(claims, ("email", "sub")), "me@example.com")
        self.assertEqual(select_claims(claims, ("email", "missing")), {"email": "me@example.com"})

    def test_invalid_token_has_no_claims(self):
        self.assertEqual(decode_jwt_claims("not-a-jwt"), {})

    def test_credential_filename_is_stable_and_safe(self):
        filename = build_credential_filename("workbuddy", "user/name@example.com", 123)

        self.assertEqual(filename, "workbuddy_usernameexample.com_123.json")


if __name__ == "__main__":
    unittest.main()
