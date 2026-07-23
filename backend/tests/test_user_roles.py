import unittest

from pydantic import TypeAdapter

from app.schemas import Role


class UserRoleSchemaTests(unittest.TestCase):
    def test_operator_is_a_valid_user_role(self) -> None:
        self.assertEqual(TypeAdapter(Role).validate_python("operator"), "operator")


if __name__ == "__main__":
    unittest.main()
