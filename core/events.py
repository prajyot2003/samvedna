"""
Typed event schema for the SAMVEDNA interaction bus.

Every message that crosses a module boundary is defined here. Channel adapters
(IVRS, portal, chatbot, app) all normalise into these types, so the triage
engine never knows which front door an interaction arrived through.

Standard library only. `core` must remain dependency-free: it is the part of
the system a reviewer should be able to audit without trusting our supply chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

class Channel(str, Enum):
    """Front door the interaction arrived through."""
    IVRS = "ivrs"
    PORTAL = "portal"
    CHATBOT = "chatbot"
    MOBILE = "mobile"


class Language(str, Enum):
    """Languages the helpline accepts.

    Membership here is not a claim of equal support. What each language
    actually gets — native recognition, a substitution, or none at all —
    is declared in `core.languages.PROFILES`, and the readiness gate reports
    every language's crisis lexicon separately. A test asserts that this enum
    and that table name exactly the same set, because a language present in one
    and missing from the other fails silently and degrades a caller's
    experience without any screen saying so.

    Selected by SC/ST (PoA) Act caseload rather than by speaker count. See the
    header of `core.languages` for why Maithili, Odia and Santali are here.
    """
    HINDI = "hi"
    BHOJPURI = "bho"
    MAITHILI = "mai"
    MARATHI = "mr"
    BENGALI = "bn"
    TELUGU = "te"
    TAMIL = "ta"
    KANNADA = "kn"
    GUJARATI = "gu"
    PUNJABI = "pa"
    ODIA = "or"
    SANTALI = "sat"

    @property
    def profile(self):
        """This language's support profile. Imported lazily so that
        `core.events` stays importable on its own."""
        from core.languages import PROFILES
        return PROFILES[self.value]


class Speaker(str, Enum):
    CALLER = "caller"
    COUNSELLOR = "counsellor"
    AGENT = "agent"          # the system's own intake prompts


class Tier(str, Enum):
    """Risk tiers. Ordered; see `tier_rank` for comparison."""
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_TIER_ORDER = [Tier.LOW, Tier.MODERATE, Tier.HIGH, Tier.CRITICAL]


def tier_rank(t: Tier) -> int:
    return _TIER_ORDER.index(t)


def escalate_one_tier(t: Tier) -> Tier:
    return _TIER_ORDER[min(tier_rank(t) + 1, len(_TIER_ORDER) - 1)]


class Confidence(str, Enum):
    """Signal quality verdict. LOW zeroes Channel C and raises the floor:
    uncertainty escalates, it never de-escalates."""
    OK = "ok"
    LOW = "low"


class FactSource(str, Enum):
    """Provenance of a Channel A fact. Only CONFIRMED facts are treated as
    established; extracted-but-unconfirmed facts count toward coverage at a
    discount and are surfaced to the counsellor as provisional."""
    COUNSELLOR = "counsellor"     # entered by a human operator
    CONFIRMED = "confirmed"       # extracted, then read back and confirmed
    EXTRACTED = "extracted"       # inferred from narrative, not yet confirmed


class ConsentScope(str, Enum):
    ANALYSIS = "analysis"     # may we score this interaction at all
    RETENTION = "retention"   # may we keep derived features after the call
    REFERRAL = "referral"     # may we share with Tele-MANAS / DLSA


class ConsentDecision(str, Enum):
    GRANTED = "granted"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class Instrument(str, Enum):
    PHQ2 = "phq2"
    PHQ9 = "phq9"
    GAD2 = "gad2"
    GAD7 = "gad7"
    PC_PTSD5 = "pc_ptsd5"
    CSSRS = "cssrs"
    IMPAIRMENT = "impairment"


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Event:
    """Base for everything on the bus."""
    interaction_id: str
    seq: int = 0
    ts: datetime = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = type(self).__name__
        d["ts"] = self.ts.isoformat()
        return d


@dataclass(frozen=True)
class InteractionStarted(Event):
    channel: Channel = Channel.IVRS
    language: Language = Language.HINDI


@dataclass(frozen=True)
class ConsentRecorded(Event):
    """Captured before any analysis begins. Declining ANALYSIS puts the
    interaction into passive mode: full human handling, no scoring."""
    scope: ConsentScope = ConsentScope.ANALYSIS
    decision: ConsentDecision = ConsentDecision.DECLINED
    language: Language = Language.HINDI
    script_version: str = ""
    method: str = ""           # "spoken" | "dtmf" | "web_checkbox"


@dataclass(frozen=True)
class Utterance(Event):
    """One recognised turn. `asr_confidence` drives the abstention path, so it
    is mandatory rather than optional: an ASR backend that cannot report
    confidence cannot be used for triage."""
    speaker: Speaker = Speaker.CALLER
    text: str = ""
    language: Language = Language.HINDI
    asr_confidence: float = 0.0
    t_start: float = 0.0
    t_end: float = 0.0


@dataclass(frozen=True)
class AcousticWindow(Event):
    """eGeMAPS functionals over one analysis window, plus the derived
    conversational features (pause ratio, response latency, speech rate)."""
    t_start: float = 0.0
    t_end: float = 0.0
    features: Dict[str, float] = field(default_factory=dict)
    snr_db: float = 0.0
    speech_ratio: float = 0.0


@dataclass(frozen=True)
class FactAsserted(Event):
    """A Channel A risk factor being established."""
    key: str = ""
    value: Any = None
    source: FactSource = FactSource.EXTRACTED
    confidence: float = 1.0


@dataclass(frozen=True)
class ScreenerResponse(Event):
    """One item of one clinical instrument."""
    instrument: Instrument = Instrument.PHQ2
    item: int = 0
    value: int = 0


@dataclass(frozen=True)
class ModelSignal(Event):
    """Channel C output. Bounded modulator; see core.svi.engine."""
    distress_probability: float = 0.0
    model_confidence: float = 0.0
    signal_confidence: Confidence = Confidence.OK
    source: str = ""           # "acoustic" | "lexical" | "fused"


@dataclass(frozen=True)
class SVIComputed(Event):
    score: float = 0.0
    tier: Tier = Tier.LOW
    channel_a: float = 0.0
    channel_b: float = 0.0
    channel_c_delta: float = 0.0
    rule_triggered: Optional[str] = None
    abstained: bool = False
    contributions: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TierOverridden(Event):
    """A human overruling the system. Always available, always recorded,
    always with a reason. The override is what enters the case record."""
    from_tier: Tier = Tier.LOW
    to_tier: Tier = Tier.LOW
    counsellor_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ActionRaised(Event):
    action_id: str = ""
    statutory_basis: str = ""
    owner: str = ""
    sla_minutes: int = 0
