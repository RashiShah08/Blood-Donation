"""Non-monetary recognition for donors.

Blood donation in India must be voluntary and unpaid (NBTC, WHO), so donors earn
badges and a certificate, never cash, coupons or gift cards.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Badge:
    donations_needed: int
    name: str
    description: str


BADGES: tuple[Badge, ...] = (
    Badge(1, "First Drop", "Completed your first donation through BloodConnect."),
    Badge(3, "Lifesaver", "Completed 3 donations."),
    Badge(5, "Hero", "Completed 5 donations."),
    Badge(10, "Legend", "Completed 10 donations."),
)


def earned_badges(donation_count: int) -> list[Badge]:
    return [badge for badge in BADGES if donation_count >= badge.donations_needed]


def next_badge(donation_count: int) -> Badge | None:
    return next((badge for badge in BADGES if donation_count < badge.donations_needed), None)
