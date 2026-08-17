from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta


NOW = datetime(2026, 8, 17, 20, 0, tzinfo=UTC)


class RiskDomainTests(unittest.TestCase):
    def test_dotted_local_part_matches_for_every_domain(self) -> None:
        from app.modules.risk.domain import match_email_rules

        for email in (
            "ta.nb.i.lly4.5@gmail.com",
            "l.imh.e.nce@gmail.com",
            "e.l.lame.d.a.raten@example.cn",
        ):
            with self.subTest(email=email):
                self.assertEqual(match_email_rules(email), ("email_local_part_dot",))

    def test_non_empty_plus_tag_matches_for_every_domain(self) -> None:
        from app.modules.risk.domain import match_email_rules

        self.assertEqual(
            match_email_rules("vtsrvja7325c+n4rp2@hotmail.com"),
            ("email_plus_tag",),
        )
        self.assertEqual(
            match_email_rules("biliangmei787611+74e18hq0eyn@outlook.com"),
            ("email_plus_tag",),
        )

    def test_email_can_match_both_rules_after_normalization(self) -> None:
        from app.modules.risk.domain import match_email_rules, normalize_email

        self.assertEqual(normalize_email(" A.B+Tag@Example.COM "), "a.b+tag@example.com")
        self.assertEqual(
            match_email_rules(" A.B+Tag@Example.COM "),
            ("email_local_part_dot", "email_plus_tag"),
        )

    def test_invalid_email_does_not_match(self) -> None:
        from app.modules.risk.domain import match_email_rules

        for email in ("", "@example.com", "person@", "a@b@example.com", "person+@example.com"):
            with self.subTest(email=email):
                self.assertEqual(match_email_rules(email), ())

    def test_ip_normalization_accepts_ipv4_and_ipv6(self) -> None:
        from app.modules.risk.domain import normalize_ip

        self.assertEqual(normalize_ip(" 14.31.212.25 "), "14.31.212.25")
        self.assertEqual(normalize_ip("2001:0db8:0:0:0:0:0:1"), "2001:db8::1")
        self.assertIsNone(normalize_ip(""))
        self.assertIsNone(normalize_ip("999.31.212.25"))

    def test_shared_ip_requires_three_distinct_accounts_within_seven_days(self) -> None:
        from app.modules.risk.domain import IpObservation, shared_ip_evidence

        observations = [
            IpObservation("1", "one@example.com", "14.31.212.25", "user_audit", NOW),
            IpObservation("2", "two@example.com", "14.31.212.25", "usage_log", NOW),
            IpObservation("2", "two@example.com", "14.31.212.25", "user_audit", NOW),
        ]
        self.assertEqual(shared_ip_evidence(observations, now=NOW), ())

        observations.append(
            IpObservation("3", "three@example.com", "14.31.212.25", "registration_audit", NOW)
        )
        evidence = shared_ip_evidence(observations, now=NOW)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].distinct_account_count, 3)
        self.assertEqual(evidence[0].sources, ("registration_audit", "usage_log", "user_audit"))

    def test_shared_ip_window_includes_exact_boundary_and_excludes_older_rows(self) -> None:
        from app.modules.risk.domain import IpObservation, shared_ip_evidence

        observations = (
            IpObservation("1", "one@example.com", "10.0.0.1", "user_audit", NOW - timedelta(days=7)),
            IpObservation("2", "two@example.com", "10.0.0.1", "user_audit", NOW),
            IpObservation("3", "three@example.com", "10.0.0.1", "usage_log", NOW),
            IpObservation("4", "old@example.com", "10.0.0.2", "usage_log", NOW - timedelta(days=7, seconds=1)),
            IpObservation("5", "five@example.com", "10.0.0.2", "usage_log", NOW),
            IpObservation("6", "six@example.com", "10.0.0.2", "usage_log", NOW),
        )

        evidence = shared_ip_evidence(observations, now=NOW)

        self.assertEqual([item.ip_address for item in evidence], ["10.0.0.1"])

    def test_decision_matrix_never_auto_bans_on_one_signal(self) -> None:
        from app.modules.risk.domain import RiskDecision, SharedIpEvidence, decide_risk

        shared = SharedIpEvidence(
            ip_address="14.31.212.25",
            distinct_account_count=3,
            external_user_ids=("1", "2", "3"),
            sources=("user_audit",),
            first_seen_at=NOW,
            last_seen_at=NOW,
        )

        self.assertEqual(
            decide_risk(email_rules=("email_plus_tag",), shared_ips=(), manual_override=False),
            RiskDecision.HIGH_RISK,
        )
        self.assertEqual(
            decide_risk(email_rules=(), shared_ips=(shared,), manual_override=False),
            RiskDecision.HIGH_RISK,
        )
        self.assertEqual(
            decide_risk(email_rules=("email_plus_tag",), shared_ips=(shared,), manual_override=False),
            RiskDecision.BAN,
        )
        self.assertEqual(
            decide_risk(email_rules=(), shared_ips=(), manual_override=False),
            RiskDecision.CLEAR,
        )

    def test_manual_override_suppresses_ban_and_high_risk(self) -> None:
        from app.modules.risk.domain import RiskDecision, SharedIpEvidence, decide_risk

        shared = SharedIpEvidence(
            ip_address="14.31.212.25",
            distinct_account_count=3,
            external_user_ids=("1", "2", "3"),
            sources=("usage_log",),
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        self.assertEqual(
            decide_risk(
                email_rules=("email_local_part_dot",),
                shared_ips=(shared,),
                manual_override=True,
            ),
            RiskDecision.CLEAR,
        )

    def test_source_health_reports_coverage_age_not_connection_health(self) -> None:
        from app.modules.risk.domain import source_health

        self.assertEqual(source_health(latest_observed_at=NOW, now=NOW), "current")
        self.assertEqual(
            source_health(latest_observed_at=NOW - timedelta(minutes=16), now=NOW),
            "delayed",
        )
        self.assertEqual(
            source_health(latest_observed_at=NOW - timedelta(days=1, seconds=1), now=NOW),
            "stale",
        )
        self.assertEqual(source_health(latest_observed_at=None, now=NOW), "empty")


if __name__ == "__main__":
    unittest.main()
