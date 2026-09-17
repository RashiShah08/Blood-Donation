"""ABO/RhD red-cell compatibility: the single source of truth for who can donate to whom.

Run ``python -m bloodconnect.domain.blood`` for a small interactive checker.
"""

BLOOD_GROUPS: tuple[str, ...] = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")

# Donor groups each recipient can safely receive red cells from.
_RECEIVE_FROM: dict[str, frozenset[str]] = {
    "A+": frozenset({"A+", "A-", "O+", "O-"}),
    "A-": frozenset({"A-", "O-"}),
    "B+": frozenset({"B+", "B-", "O+", "O-"}),
    "B-": frozenset({"B-", "O-"}),
    "AB+": frozenset(BLOOD_GROUPS),  # universal recipient
    "AB-": frozenset({"A-", "B-", "AB-", "O-"}),
    "O+": frozenset({"O+", "O-"}),
    "O-": frozenset({"O-"}),
}


def normalize_blood_group(value: object) -> str | None:
    """Return the canonical form ("AB-") of a blood group, or None if it isn't one."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper().replace(" ", "").replace("−", "-")
    return cleaned if cleaned in _RECEIVE_FROM else None


def _require(group: str) -> str:
    normalized = normalize_blood_group(group)
    if normalized is None:
        raise ValueError(f"Unknown blood group: {group!r}")
    return normalized


def can_donate(donor_group: str, recipient_group: str) -> bool:
    return _require(donor_group) in _RECEIVE_FROM[_require(recipient_group)]


def donor_groups_for(recipient_group: str) -> tuple[str, ...]:
    """Donor groups compatible with a recipient, in the standard display order."""
    allowed = _RECEIVE_FROM[_require(recipient_group)]
    return tuple(group for group in BLOOD_GROUPS if group in allowed)


def recipient_groups_for(donor_group: str) -> tuple[str, ...]:
    """Recipient groups a donor can give to, in the standard display order."""
    donor = _require(donor_group)
    return tuple(group for group in BLOOD_GROUPS if donor in _RECEIVE_FROM[group])


def main() -> None:
    role = input("Are you a donor or a patient? (donor/patient): ").strip().lower()
    group = normalize_blood_group(input("Enter the blood group (e.g. A+, O-, B+): "))
    if group is None:
        print("Invalid blood group entered.")
    elif role == "donor":
        print(f"\nAs a {group} donor you can donate to: {', '.join(recipient_groups_for(group))}")
    elif role == "patient":
        print(f"\nA {group} patient can receive from: {', '.join(donor_groups_for(group))}")
    else:
        print("Please type either 'donor' or 'patient'.")


if __name__ == "__main__":
    main()
