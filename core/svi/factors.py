"""
Channel A — structured context.

An objective, weighted risk-factor checklist. This is the highest-weighted
channel in the SVI, and it contains no machine learning at all. Structured
risk factors are what actually predict vulnerability; the AI channel exists to
modulate this, not to replace it.

Two independent components:

  * OFFENCE SEVERITY — a graded 1..5 scale keyed to the offence category under
    the SC/ST (PoA) Act. Exactly one applies; they are not additive.
  * AGGRAVATING FACTORS — independent binary conditions that compound risk.
    These are additive.

Normalisation. Summing every aggravating weight as the denominator would mean a
case needs all eighteen factors to reach 1.0, which never happens and would
compress every real case into the Low band. Instead the denominator saturates
at the sum of the SATURATION_K heaviest weights: a case carrying the six worst
aggravating factors is already at maximum structural risk, and further factors
cannot push it higher. This is a deliberate, documented choice, not a fitted
parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Set, Tuple


@dataclass(frozen=True)
class Factor:
    key: str
    weight: int          # 1..5
    label_en: str
    label_hi: str
    confirm: bool        # must be read back and confirmed before it counts fully


# --------------------------------------------------------------------------
# Offence severity — exactly one applies
# --------------------------------------------------------------------------

OFFENCE_SEVERITY: Dict[str, int] = {
    "murder":                      5,
    "gang_rape":                   5,
    "rape":                        5,
    "grievous_hurt":               4,
    "sexual_assault":              4,
    "arson_property_destruction":  4,
    "land_dispossession":          3,
    "social_boycott":              3,
    "wrongful_confinement":        3,
    "intimidation_threat":         2,
    "public_humiliation":          2,
    "verbal_abuse_caste_slur":     2,
    "denial_of_access":            2,
    "unspecified":                 1,
}

OFFENCE_LABELS_HI: Dict[str, str] = {
    "murder": "हत्या",
    "gang_rape": "सामूहिक बलात्कार",
    "rape": "बलात्कार",
    "grievous_hurt": "गंभीर चोट",
    "sexual_assault": "यौन उत्पीड़न",
    "arson_property_destruction": "आगजनी / संपत्ति क्षति",
    "land_dispossession": "भूमि से बेदखली",
    "social_boycott": "सामाजिक बहिष्कार",
    "wrongful_confinement": "गलत तरीके से बंधक बनाना",
    "intimidation_threat": "धमकी",
    "public_humiliation": "सार्वजनिक अपमान",
    "verbal_abuse_caste_slur": "जातिसूचक गाली",
    "denial_of_access": "प्रवेश से वंचित करना",
    "unspecified": "अनिर्दिष्ट",
}

MAX_OFFENCE_SEVERITY = 5


# --------------------------------------------------------------------------
# Aggravating factors — independent and additive
# --------------------------------------------------------------------------

AGGRAVATING_FACTORS: Tuple[Factor, ...] = (
    Factor("threat_imminent",             5, "Imminent threat to life",              "जान को तत्काल खतरा",            True),
    Factor("accused_at_large_nearby",     5, "Accused at large in same locality",    "आरोपी उसी क्षेत्र में मुक्त",     True),
    Factor("victim_minor",                4, "Victim is a minor",                    "पीड़ित नाबालिग है",              True),
    Factor("sole_earner_lost",            4, "Sole earning member lost",             "एकमात्र कमाने वाला खोया",        True),
    Factor("displaced_from_home",         4, "Displaced from home or village",       "घर/गाँव से विस्थापित",           True),
    Factor("social_boycott_active",       4, "Active social boycott",                "सक्रिय सामाजिक बहिष्कार",        True),
    Factor("police_refused_registration", 4, "Police refused to register FIR",       "पुलिस ने FIR दर्ज नहीं की",       True),
    Factor("prior_threats",               4, "Prior threats or intimidation",        "पूर्व में धमकी",                  False),
    Factor("victim_pregnant",             3, "Victim is pregnant",                   "पीड़िता गर्भवती है",              True),
    Factor("victim_disabled",             3, "Victim has a disability",              "पीड़ित दिव्यांग है",              True),
    Factor("economic_blockade",           3, "Economic blockade or wage denial",     "आर्थिक नाकेबंदी",                False),
    Factor("fir_not_registered",          3, "FIR not yet registered",               "FIR अभी दर्ज नहीं",              False),
    Factor("prior_complaint_same_accused",3, "Prior complaint, same accused",        "उसी आरोपी पर पूर्व शिकायत",      False),
    Factor("multiple_victims",            3, "Multiple victims affected",            "एक से अधिक पीड़ित",              False),
    Factor("no_family_support",           3, "No family or community support",       "पारिवारिक सहारा नहीं",           False),
    Factor("witness_pressure",            3, "Witnesses under pressure",             "गवाहों पर दबाव",                 False),
    Factor("victim_alone",                2, "Victim widowed or living alone",       "पीड़ित अकेला/विधवा",             False),
    Factor("external_pressure",           2, "Political or media pressure",          "राजनीतिक/मीडिया दबाव",           False),
)

FACTORS_BY_KEY: Dict[str, Factor] = {f.key: f for f in AGGRAVATING_FACTORS}

SATURATION_K = 6
_SORTED_WEIGHTS = sorted((f.weight for f in AGGRAVATING_FACTORS), reverse=True)
SATURATION_DENOMINATOR = float(sum(_SORTED_WEIGHTS[:SATURATION_K]))   # = 26.0

# Relative contribution of the two components to Channel A.
W_OFFENCE = 0.45
W_AGGRAVATING = 0.55

# Factors whose absence most damages the reliability of Channel A. Coverage is
# measured over these, because a case where we never established whether the
# accused is nearby is a case we have not actually assessed.
CORE_COVERAGE_KEYS: Tuple[str, ...] = (
    "threat_imminent",
    "accused_at_large_nearby",
    "prior_threats",
    "fir_not_registered",
    "social_boycott_active",
    "victim_minor",
    "displaced_from_home",
)

# An extracted-but-unconfirmed fact is real information, but weaker evidence
# than one the caller confirmed on a read-back. It counts at a discount.
UNCONFIRMED_DISCOUNT = 0.6


@dataclass
class ContextFacts:
    """Established Channel A state for one interaction."""
    offence_category: str = "unspecified"
    present: Set[str] = None              # aggravating factor keys, confirmed
    unconfirmed: Set[str] = None          # extracted but not read back
    asked: Set[str] = None                # keys the intake agent has put to the caller

    def __post_init__(self) -> None:
        self.present = set(self.present or ())
        self.unconfirmed = set(self.unconfirmed or ())
        self.asked = set(self.asked or ())
        unknown = (self.present | self.unconfirmed) - set(FACTORS_BY_KEY)
        if unknown:
            raise ValueError(f"unknown risk factor(s): {sorted(unknown)}")
        if self.offence_category not in OFFENCE_SEVERITY:
            raise ValueError(f"unknown offence category: {self.offence_category}")

    # -- scoring ----------------------------------------------------------

    def offence_component(self) -> float:
        return OFFENCE_SEVERITY[self.offence_category] / MAX_OFFENCE_SEVERITY

    def aggravating_component(self) -> float:
        confirmed = sum(FACTORS_BY_KEY[k].weight for k in self.present)
        provisional = sum(
            FACTORS_BY_KEY[k].weight * UNCONFIRMED_DISCOUNT
            for k in self.unconfirmed - self.present
        )
        return min(1.0, (confirmed + provisional) / SATURATION_DENOMINATOR)

    def score(self) -> float:
        """Channel A on 0..1."""
        return (W_OFFENCE * self.offence_component()
                + W_AGGRAVATING * self.aggravating_component())

    def coverage(self) -> float:
        """Fraction of core risk questions actually put to the caller. Drives
        the abstention path: a thin assessment must not read as a safe one."""
        if not CORE_COVERAGE_KEYS:
            return 1.0
        answered = sum(1 for k in CORE_COVERAGE_KEYS if k in self.asked)
        return answered / len(CORE_COVERAGE_KEYS)

    def contributions(self) -> Dict[str, float]:
        """Per-factor contribution to the final 0..100 scale, for the console's
        'why this score' panel. Explainability is built in from the start, not
        retrofitted."""
        out: Dict[str, float] = {
            f"offence:{self.offence_category}":
                W_OFFENCE * self.offence_component(),
        }
        for k in self.present:
            f = FACTORS_BY_KEY[k]
            out[f"factor:{k}"] = W_AGGRAVATING * f.weight / SATURATION_DENOMINATOR
        for k in self.unconfirmed - self.present:
            f = FACTORS_BY_KEY[k]
            out[f"factor:{k}(unconfirmed)"] = (
                W_AGGRAVATING * f.weight * UNCONFIRMED_DISCOUNT / SATURATION_DENOMINATOR
            )
        return out


def top_factors(facts: ContextFacts, n: int = 5) -> List[Tuple[str, float]]:
    """The n heaviest contributors, for the counsellor console."""
    return sorted(facts.contributions().items(), key=lambda kv: -kv[1])[:n]
