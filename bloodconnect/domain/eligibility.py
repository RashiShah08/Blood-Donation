"""Donor eligibility rules, based on India's NBTC blood donor selection guidelines.

The questionnaire is a screening aid only. The medical officer at the blood bank
always makes the final decision.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

MIN_AGE_YEARS = 18
MAX_AGE_YEARS = 65
MIN_WEIGHT_KG = 45
DONATION_INTERVAL_DAYS = {"Male": 90, "Female": 120}
# Use the longer gap when gender isn't Male/Female, to stay on the safe side.
DEFAULT_INTERVAL_DAYS = 120
ANSWERS = ("yes", "no")


@dataclass(frozen=True)
class Question:
    key: str
    text: str
    safe_answer: str
    reason: str


QUESTIONS: tuple[Question, ...] = (
    Question(
        "q1",
        "In the past 15 days, have you had a cold, cough or fever, taken antibiotics, "
        "or received a tetanus or COVID-19 vaccine?",
        "no",
        "A recent illness, antibiotics or vaccination (wait 15 days).",
    ),
    Question(
        "q2",
        "In the past month, have you travelled outside India or received a yellow fever, measles or mumps vaccine?",
        "no",
        "Recent travel abroad or a live vaccine (wait 1 month).",
    ),
    Question(
        "q3",
        "Do you currently have, or have you recently had, oral polio, typhoid or cholera (illness or vaccine)?",
        "no",
        "Recent oral polio, typhoid or cholera.",
    ),
    Question(
        "q4",
        "Have you had malaria in the past 3 months?",
        "no",
        "Malaria in the past 3 months.",
    ),
    Question(
        "q5",
        "In the past 6 months, have you had dengue, chikungunya, minor surgery, an abortion or a tooth extraction?",
        "no",
        "Dengue, chikungunya, minor surgery, abortion or tooth extraction in the past 6 months.",
    ),
    Question(
        "q6",
        "In the past year, have you had major surgery, a tattoo, a body piercing, typhoid, "
        "or an animal bite treated with anti-rabies immunoglobulin?",
        "no",
        "Major surgery, tattoo, piercing, typhoid or anti-rabies immunoglobulin in the past year.",
    ),
    Question(
        "q7",
        "In the past year, have you received hepatitis B immunoglobulin or a blood transfusion, "
        "or had jaundice caused by hepatitis A or E?",
        "no",
        "Hepatitis B immunoglobulin, a transfusion or hepatitis A/E jaundice in the past year.",
    ),
    Question(
        "q8",
        "Have you had tuberculosis (TB) in the past 2 years?",
        "no",
        "Tuberculosis in the past 2 years.",
    ),
    Question(
        "q9",
        "Have you ever been diagnosed with cancer, heart disease, chronic kidney or liver "
        "disease, epilepsy, a bleeding disorder, leprosy, kala-azar, unexplained weight loss, "
        "schizophrenia, insulin-dependent diabetes, HIV, hepatitis B or C, jaundice of unknown "
        "cause, or an endocrine disorder?",
        "no",
        "A condition that permanently rules out blood donation.",
    ),
    Question(
        "q10",
        "Are you currently menstruating, breastfeeding, or within one year of giving birth? "
        "(Answer No if this doesn't apply to you.)",
        "no",
        "Menstruation, breastfeeding or childbirth in the past year.",
    ),
    Question(
        "q11",
        "Do you understand that you must wait at least 3 months (men) or 4 months (women) "
        "between whole blood donations?",
        "yes",
        "Please make sure you know the minimum gap between donations before you donate.",
    ),
)


@dataclass
class EligibilityResult:
    eligible: bool
    reasons: list[str] = field(default_factory=list)


def evaluate_answers(answers: Mapping[str, str]) -> EligibilityResult:
    """Check questionnaire answers. Every question must be answered 'yes' or 'no'."""
    if any(answers.get(question.key) not in ANSWERS for question in QUESTIONS):
        return EligibilityResult(False, ["Please answer every question."])
    reasons = [q.reason for q in QUESTIONS if answers[q.key] != q.safe_answer]
    return EligibilityResult(not reasons, reasons)


def age_on(date_of_birth: date, today: date) -> int:
    had_birthday = (today.month, today.day) >= (date_of_birth.month, date_of_birth.day)
    return today.year - date_of_birth.year - (0 if had_birthday else 1)


def donation_interval_days(gender: str | None) -> int:
    return DONATION_INTERVAL_DAYS.get(gender or "", DEFAULT_INTERVAL_DAYS)


def next_eligible_date(gender: str | None, last_donation_date: date | None) -> date | None:
    """The first day the donor may give whole blood again, or None if they never have."""
    if last_donation_date is None:
        return None
    return last_donation_date + timedelta(days=donation_interval_days(gender))


def check_profile(
    *,
    date_of_birth: date,
    weight_kg: float,
    gender: str | None,
    last_donation_date: date | None,
    today: date,
) -> EligibilityResult:
    """Check the parts of eligibility that come from the donor's profile."""
    reasons = []
    age = age_on(date_of_birth, today)
    if age < MIN_AGE_YEARS or age > MAX_AGE_YEARS:
        reasons.append(f"Donors must be {MIN_AGE_YEARS} to {MAX_AGE_YEARS} years old.")
    if weight_kg < MIN_WEIGHT_KG:
        reasons.append(f"Donors must weigh at least {MIN_WEIGHT_KG} kg.")
    next_date = next_eligible_date(gender, last_donation_date)
    if next_date is not None and today < next_date:
        reasons.append(f"Your last donation was too recent. You can donate again from {next_date:%d %b %Y}.")
    return EligibilityResult(not reasons, reasons)
