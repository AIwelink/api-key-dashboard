import unittest

from pydantic import TypeAdapter, ValidationError

from app.schemas import Role, UserCreate


class UserRoleSchemaTests(unittest.TestCase):
    def test_operator_is_a_valid_user_role(self) -> None:
        self.assertEqual(TypeAdapter(Role).validate_python("operator"), "operator")

    def test_custom_role_is_valid_for_users(self) -> None:
        payload = UserCreate(
            email="support@example.com",
            name="Support",
            role="support-team",
            password="password123",
        )

        self.assertEqual(payload.role, "support-team")

    def test_invalid_custom_role_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            UserCreate(
                email="support@example.com",
                name="Support",
                role="Support Team",
                password="password123",
            )


if __name__ == "__main__":
    unittest.main()
