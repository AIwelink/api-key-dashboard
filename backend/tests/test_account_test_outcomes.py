import unittest


from app.modules.sub2api.account_test_outcomes import (
    classify_test_result,
    disable_reason,
    has_http_status,
    snapshot_has_http_status,
)


class AccountTestOutcomeTests(unittest.TestCase):
    def test_http_status_detection_is_exact_and_reads_cached_snapshot(self) -> None:
        self.assertTrue(has_http_status("API returned 403: forbidden", 403))
        self.assertTrue(snapshot_has_http_status({"status": "403"}, 403))
        self.assertTrue(
            snapshot_has_http_status(
                {"account": {"error_message": "API returned 403"}},
                403,
            )
        )
        self.assertFalse(has_http_status("wait 4030 milliseconds", 403))

    def test_classifies_supported_account_results(self) -> None:
        self.assertEqual(classify_test_result({"success": True}), "passed")
        self.assertEqual(classify_test_result({"error": "API returned 429"}), "rate_limited")
        self.assertEqual(
            classify_test_result({"error": "API returned 401: token_invalidated"}),
            "unauthorized",
        )
        self.assertEqual(
            classify_test_result({"error": "API returned 402: deactivated_workspace"}),
            "payment_required",
        )
        self.assertEqual(
            classify_test_result(
                {"error": "API returned 403: Personal access token owner is inactive"}
            ),
            "inactive_owner",
        )
        self.assertEqual(
            classify_test_result({"error": "API returned 403: model_not_allowed"}),
            "forbidden_other",
        )
        self.assertEqual(
            classify_test_result(
                {
                    "error": (
                        "model is not supported when using codex with a chatgpt account"
                    )
                }
            ),
            "model_not_supported",
        )

    def test_transport_and_unclassified_failures_remain_distinct(self) -> None:
        self.assertEqual(
            classify_test_result(transport_error="connection reset"),
            "transport_error",
        )
        self.assertEqual(classify_test_result({"success": False}), "failed")

    def test_disable_reason_is_limited_to_confirmed_account_failures(self) -> None:
        self.assertEqual(disable_reason("unauthorized"), "token_invalidated")
        self.assertEqual(disable_reason("payment_required"), "deactivated_workspace")
        self.assertEqual(disable_reason("inactive_owner"), "inactive_token_owner")
        self.assertIsNone(disable_reason("forbidden_other"))
        self.assertIsNone(disable_reason("rate_limited"))


if __name__ == "__main__":
    unittest.main()
