"""Unit tests for the pure business rules: compatibility, eligibility, geography, recognition."""

import math
from datetime import date, timedelta

import pytest

from bloodconnect.domain import blood, eligibility, geo, recognition

# Red cell compatibility written out independently of the implementation.
EXPECTED_RECEIVE_FROM = {
    "O-": {"O-"},
    "O+": {"O-", "O+"},
    "A-": {"O-", "A-"},
    "A+": {"O-", "O+", "A-", "A+"},
    "B-": {"O-", "B-"},
    "B+": {"O-", "O+", "B-", "B+"},
    "AB-": {"O-", "A-", "B-", "AB-"},
    "AB+": {"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"},
}


class TestBloodCompatibility:
    @pytest.mark.parametrize("recipient", blood.BLOOD_GROUPS)
    @pytest.mark.parametrize("donor", blood.BLOOD_GROUPS)
    def test_full_matrix(self, donor, recipient):
        assert blood.can_donate(donor, recipient) == (donor in EXPECTED_RECEIVE_FROM[recipient])

    def test_o_negative_is_universal_donor(self):
        assert blood.recipient_groups_for("O-") == blood.BLOOD_GROUPS

    def test_ab_positive_is_universal_recipient(self):
        assert blood.donor_groups_for("AB+") == blood.BLOOD_GROUPS

    @pytest.mark.parametrize("group", blood.BLOOD_GROUPS)
    def test_donor_and_recipient_views_agree(self, group):
        for other in blood.recipient_groups_for(group):
            assert group in blood.donor_groups_for(other)

    def test_results_keep_display_order(self):
        assert blood.donor_groups_for("A+") == ("A+", "A-", "O+", "O-")

    @pytest.mark.parametrize("raw, expected", [(" ab- ", "AB-"), ("o+", "O+"), ("O−", "O-"), ("A +", "A+")])
    def test_normalize_accepts_common_variants(self, raw, expected):
        assert blood.normalize_blood_group(raw) == expected

    @pytest.mark.parametrize("raw", ["C+", "", "AB", None, 5, "O--"])
    def test_normalize_rejects_invalid(self, raw):
        assert blood.normalize_blood_group(raw) is None

    def test_unknown_group_raises(self):
        with pytest.raises(ValueError):
            blood.can_donate("Z+", "A+")
        with pytest.raises(ValueError):
            blood.donor_groups_for("nope")

    def test_cli_prints_compatible_groups(self, monkeypatch, capsys):
        answers = iter(["patient", "b-"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
        blood.main()
        assert "B-, O-" in capsys.readouterr().out


SAFE = {**{f"q{i}": "no" for i in range(1, 11)}, "q11": "yes"}


class TestQuestionnaire:
    def test_there_are_eleven_questions_with_unique_keys(self):
        keys = [q.key for q in eligibility.QUESTIONS]
        assert keys == [f"q{i}" for i in range(1, 12)]

    def test_safe_answers_are_eligible(self):
        result = eligibility.evaluate_answers(SAFE)
        assert result.eligible and result.reasons == []

    @pytest.mark.parametrize("key", [f"q{i}" for i in range(1, 11)])
    def test_any_yes_on_health_questions_is_ineligible(self, key):
        result = eligibility.evaluate_answers({**SAFE, key: "yes"})
        assert not result.eligible
        assert len(result.reasons) == 1

    def test_awareness_question_must_be_answered_yes(self):
        """Regression: the old dashboard required 'no' to every question, including this one."""
        result = eligibility.evaluate_answers({**SAFE, "q11": "no"})
        assert not result.eligible
        assert "minimum gap" in result.reasons[0]

    def test_all_no_including_awareness_is_ineligible(self):
        assert not eligibility.evaluate_answers({f"q{i}": "no" for i in range(1, 12)}).eligible

    def test_missing_or_invalid_answers(self):
        missing = dict(SAFE)
        missing.pop("q5")
        assert eligibility.evaluate_answers(missing).reasons == ["Please answer every question."]
        assert not eligibility.evaluate_answers({**SAFE, "q3": "maybe"}).eligible

    def test_multiple_reasons_are_reported(self):
        result = eligibility.evaluate_answers({**SAFE, "q1": "yes", "q6": "yes"})
        assert len(result.reasons) == 2


class TestProfileEligibility:
    today = date(2026, 9, 14)

    def check(self, **overrides):
        values = {
            "date_of_birth": date(1996, 1, 1),
            "weight_kg": 60,
            "gender": "Male",
            "last_donation_date": None,
            "today": self.today,
        }
        values.update(overrides)
        return eligibility.check_profile(**values)

    def test_typical_donor_is_eligible(self):
        assert self.check().eligible

    def test_age_boundaries(self):
        assert not self.check(date_of_birth=date(2008, 9, 15)).eligible  # turns 18 tomorrow
        assert self.check(date_of_birth=date(2008, 9, 14)).eligible  # 18 today
        assert self.check(date_of_birth=date(1961, 9, 15)).eligible  # 64, turns 65 tomorrow
        assert not self.check(date_of_birth=date(1960, 9, 14)).eligible  # 66 today

    def test_minimum_weight(self):
        assert not self.check(weight_kg=44.9).eligible
        assert self.check(weight_kg=45).eligible

    @pytest.mark.parametrize("gender, days", [("Male", 90), ("Female", 120), ("Other", 120)])
    def test_donation_interval(self, gender, days):
        too_soon = self.today - timedelta(days=days - 1)
        long_enough = self.today - timedelta(days=days)
        assert not self.check(gender=gender, last_donation_date=too_soon).eligible
        assert self.check(gender=gender, last_donation_date=long_enough).eligible

    def test_next_eligible_date(self):
        assert eligibility.next_eligible_date("Female", None) is None
        assert eligibility.next_eligible_date("Female", date(2026, 1, 1)) == date(2026, 5, 1)

    def test_age_on_birthday_edges(self):
        assert eligibility.age_on(date(2000, 2, 29), date(2026, 2, 28)) == 25
        assert eligibility.age_on(date(2000, 2, 29), date(2026, 3, 1)) == 26


class TestGeo:
    def test_zero_distance(self):
        assert geo.haversine_km(19.0, 72.8, 19.0, 72.8) == 0

    def test_mumbai_to_pune(self):
        distance = geo.haversine_km(18.9398, 72.8355, 18.5204, 73.8567)
        assert 115 < distance < 125
        assert math.isclose(distance, geo.haversine_km(18.5204, 73.8567, 18.9398, 72.8355))

    def test_bounding_box_contains_points_at_radius(self):
        lat, lon, radius = 19.1136, 72.8697, 5
        min_lat, max_lat, min_lon, max_lon = geo.bounding_box(lat, lon, radius)
        step = radius / 111.2
        for point in [(lat + step * 0.99, lon), (lat - step * 0.99, lon)]:
            assert min_lat <= point[0] <= max_lat
        east = lon + radius / (111.32 * math.cos(math.radians(lat))) * 0.99
        assert min_lon <= east <= max_lon

    def test_bounding_box_near_poles_and_antimeridian(self):
        assert geo.bounding_box(89.99, 0, 50)[2:] == (None, None)
        assert geo.bounding_box(0, 179.99, 50)[2:] == (None, None)

    @pytest.mark.parametrize(
        "lat, lon, valid",
        [
            (0, 0, True),
            (90, 180, True),
            (-90, -180, True),
            (90.1, 0, False),
            (0, -181, False),
            (float("nan"), 0, False),
            (float("inf"), 0, False),
            ("19", "72", False),
            (True, 72, False),
            (None, 1, False),
        ],
    )
    def test_coordinate_validation(self, lat, lon, valid):
        assert geo.is_valid_coordinate(lat, lon) is valid

    def test_approximate_rounds_to_about_a_kilometre(self):
        assert geo.approximate(19.123456, 72.876543) == (19.12, 72.88)


class TestRecognition:
    def test_badges_unlock_progressively(self):
        assert recognition.earned_badges(0) == []
        assert [b.name for b in recognition.earned_badges(3)] == ["First Drop", "Lifesaver"]
        assert recognition.next_badge(3).name == "Hero"
        assert recognition.next_badge(10) is None

    def test_no_monetary_rewards(self):
        text = " ".join(f"{b.name} {b.description}" for b in recognition.BADGES).lower()
        for word in ("₹", "cash", "coupon", "gift card", "money"):
            assert word not in text
