"""
The intake agent.

A dialog policy, not an open-ended conversational model. It decides what to ask
next from explicit state, and every decision it makes can be read off that
state and argued with. A generative model deciding for itself whether to
administer a suicide screener is not something this system will do.

THE AGENT SUGGESTS; THE COUNSELLOR ASKS. In live operation the next prompt
appears on the counsellor's screen and a person says it. The agent's job is to
make sure nothing important is missed on a call where someone is describing the
worst thing that has happened to them, not to replace the person listening.
On the automated channels (IVRS, chatbot) it speaks directly, and the same
policy governs both.

FOUR RULES THAT ARE NOT NEGOTIABLE, each enforced here and covered by a test:

  1. Consent precedes analysis. Nothing is scored before the analysis scope is
     granted, and declining costs the caller nothing.
  2. The C-SSRS is always administered. Never conditional on a score, a model,
     or how the caller sounds.
  3. Crisis language interrupts everything. A self-harm indicator at any point
     jumps straight to the suicide screener; the remaining risk-factor
     questions can wait, and asking about land records while someone is
     disclosing suicidal intent is its own kind of harm.
  4. Facts that move a tier are read back before they count. Extraction
     proposes; the caller confirms.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Optional, Sequence, Set, Tuple

from core.events import ConsentDecision, ConsentScope, Instrument, Language
from core.svi.factors import ContextFacts
from core.svi.instruments import CSSRSScreen, Screeners
from services.intake import schedule as S
from services.nlp.facts import Extraction
from services.nlp.lexicon import LexiconAnalysis


class Phase(str, Enum):
    CONSENT = "consent"
    OPENING = "opening"
    SLOTS = "slots"
    CONFIRMING = "confirming"
    SCREENERS = "screeners"
    CRISIS_SCREEN = "crisis_screen"
    CLOSING = "closing"
    DONE = "done"
    PASSIVE = "passive"            # analysis consent declined


class ActionKind(str, Enum):
    ASK_CONSENT = "ask_consent"
    OPEN_NARRATIVE = "open_narrative"
    ASK_SLOT = "ask_slot"
    CONFIRM_FACT = "confirm_fact"
    ASK_SCREENER = "ask_screener"
    CRISIS_HANDOVER = "crisis_handover"
    CLOSE = "close"
    ACKNOWLEDGE = "acknowledge"


@dataclass(frozen=True)
class AgentAction:
    kind: ActionKind
    prompt: str
    language: Language
    slot_key: Optional[str] = None
    instrument: Optional[Instrument] = None
    item_index: Optional[int] = None
    scope: Optional[ConsentScope] = None
    scale: Optional[str] = None
    rationale: str = ""            # shown to the counsellor, not the caller


@dataclass
class IntakeState:
    """Everything the policy reads. Serialisable, inspectable, and the same
    object the console renders."""
    language: Language = Language.HINDI
    phase: Phase = Phase.CONSENT

    consent: Dict[str, str] = field(default_factory=dict)
    narrative_given: bool = False

    asked_slots: Set[str] = field(default_factory=set)
    confirmed: Set[str] = field(default_factory=set)
    unconfirmed: Set[str] = field(default_factory=set)
    pending_confirmation: List[str] = field(default_factory=list)
    offence_category: str = "unspecified"

    phq: List[int] = field(default_factory=list)
    gad: List[int] = field(default_factory=list)
    ptsd: List[int] = field(default_factory=list)
    cssrs_answers: List[bool] = field(default_factory=list)
    impairment: Optional[int] = None

    crisis_flag: bool = False
    crisis_handover_done: bool = False

    def analysis_granted(self) -> bool:
        return self.consent.get(ConsentScope.ANALYSIS.value) == ConsentDecision.GRANTED.value

    # -- projections into the scoring layer -----------------------------

    def to_context_facts(self) -> ContextFacts:
        return ContextFacts(offence_category=self.offence_category,
                            present=set(self.confirmed),
                            unconfirmed=set(self.unconfirmed),
                            asked=set(self.asked_slots))

    def to_screeners(self) -> Screeners:
        cssrs = CSSRSScreen(administered=len(self.cssrs_answers) == len(S.CSSRS_ITEMS))
        if self.cssrs_answers:
            padded = self.cssrs_answers + [False] * (6 - len(self.cssrs_answers))
            cssrs = CSSRSScreen(administered=cssrs.administered, q1=padded[0],
                                q2=padded[1], q3=padded[2], q4=padded[3],
                                q5=padded[4], q6=padded[5])
        return Screeners(
            phq2=self.phq[:2] if len(self.phq) >= 2 else None,
            phq9=self.phq if len(self.phq) == 9 else None,
            gad2=self.gad[:2] if len(self.gad) >= 2 else None,
            gad7=self.gad if len(self.gad) == 7 else None,
            pc_ptsd5=self.ptsd if len(self.ptsd) == 5 else None,
            impairment=self.impairment,
            cssrs=cssrs,
        )


class IntakeAgent:
    """Stateless policy over an explicit `IntakeState`."""

    # Consent scopes in the order they are sought. Analysis first: everything
    # else is moot if it is declined.
    CONSENT_ORDER = (ConsentScope.ANALYSIS, ConsentScope.RETENTION,
                     ConsentScope.REFERRAL)

    PHQ_STEM = 2
    GAD_STEM = 2
    PHQ_ESCALATE_AT = 3
    GAD_ESCALATE_AT = 3

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_narrative(self, state: IntakeState, extraction: Extraction,
                         lexicon: Optional[LexiconAnalysis] = None) -> IntakeState:
        """Fold what the caller said into state.

        Extracted facts land as UNCONFIRMED. Those whose slots require
        confirmation are queued for read-back; the rest stay provisional and
        count at a discount, exactly as `ContextFacts` treats them.
        """
        state.narrative_given = True

        if extraction.offence_category and state.offence_category == "unspecified":
            state.offence_category = extraction.offence_category

        for fact in extraction.facts:
            if fact.key in state.confirmed:
                continue
            state.unconfirmed.add(fact.key)
            slot = S.SLOTS_BY_KEY.get(fact.key)
            if (slot and slot.confirm and fact.key not in state.pending_confirmation
                    and fact.key in S.CONFIRMATION_STATEMENTS):
                state.pending_confirmation.append(fact.key)

        if lexicon is not None and "self_harm" in lexicon.rule_categories:
            # Rule 3. Everything else waits.
            state.crisis_flag = True

        return state

    def record_consent(self, state: IntakeState, scope: ConsentScope,
                       decision: ConsentDecision) -> IntakeState:
        state.consent[scope.value] = decision.value
        if scope is ConsentScope.ANALYSIS and decision is not ConsentDecision.GRANTED:
            state.phase = Phase.PASSIVE
        return state

    def record_crisis_handover(self, state: IntakeState) -> IntakeState:
        """Marks the live handover as accepted by a counsellor.

        Set only when a person has actually taken the call, never on dialling.
        Until it is set the agent keeps returning the handover action, which is
        the correct behaviour: a caller who has disclosed suicidal intent is not
        released from the line because a transfer was attempted.
        """
        if not state.crisis_flag:
            raise ValueError("no crisis handover is in progress")
        state.crisis_handover_done = True
        return state

    def record_slot(self, state: IntakeState, key: str, present: bool,
                    confirmed: bool = True) -> IntakeState:
        """A slot the caller answered directly. Asked either way — coverage
        counts the question, not the answer."""
        state.asked_slots.add(key)
        state.unconfirmed.discard(key)
        if key in state.pending_confirmation:
            state.pending_confirmation.remove(key)
        if present and confirmed:
            state.confirmed.add(key)
        elif present:
            state.unconfirmed.add(key)
        else:
            state.confirmed.discard(key)
        return state

    def record_screener(self, state: IntakeState, instrument: Instrument,
                        index: int, value: int) -> IntakeState:
        if instrument in (Instrument.PHQ2, Instrument.PHQ9):
            _set_at(state.phq, index, int(value))
            if index == 8 and value > 0:
                state.crisis_flag = True      # PHQ-9 item 9 is a safety signal
        elif instrument in (Instrument.GAD2, Instrument.GAD7):
            _set_at(state.gad, index, int(value))
        elif instrument is Instrument.PC_PTSD5:
            _set_at(state.ptsd, index, int(value))
        elif instrument is Instrument.CSSRS:
            _set_at(state.cssrs_answers, index, bool(value))
        elif instrument is Instrument.IMPAIRMENT:
            state.impairment = int(value)
        return state

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def next_action(self, state: IntakeState) -> AgentAction:
        lang = state.language

        # Rule 1 — consent before anything.
        for scope in self.CONSENT_ORDER:
            if scope.value not in state.consent:
                if scope is not ConsentScope.ANALYSIS and not state.analysis_granted():
                    break
                return AgentAction(
                    ActionKind.ASK_CONSENT, S.CONSENT_PROMPTS[scope.value][lang.value],
                    lang, scope=scope,
                    rationale=f"consent required for '{scope.value}' before proceeding")

        if state.phase is Phase.PASSIVE or not state.analysis_granted():
            return AgentAction(
                ActionKind.ACKNOWLEDGE,
                S.CONSENT_DECLINED_ACKNOWLEDGEMENT[lang.value], lang,
                rationale="caller declined analysis; passive mode, no scoring, "
                          "full human handling")

        # Rule 3 — crisis language interrupts everything else.
        if state.crisis_flag and not self._cssrs_complete(state):
            index = len(state.cssrs_answers)
            item = S.CSSRS_ITEMS[index]
            return AgentAction(
                ActionKind.ASK_SCREENER, item.prompt(lang), lang,
                instrument=Instrument.CSSRS, item_index=index, scale=item.scale,
                rationale="crisis indicator detected; suicide screener brought "
                          "forward ahead of all other questions")

        if state.crisis_flag and not state.crisis_handover_done:
            return AgentAction(
                ActionKind.CRISIS_HANDOVER, S.CRISIS_HANDOVER_PROMPT[lang.value], lang,
                rationale="crisis screen complete; live handover to a counsellor "
                          "before the call ends")

        if not state.narrative_given:
            return AgentAction(ActionKind.OPEN_NARRATIVE, S.OPENING_PROMPT[lang.value],
                               lang, rationale="open question first; people disclose "
                                               "more in their own account")

        # Rule 4 — read back tier-relevant facts before they count.
        if state.pending_confirmation:
            key = state.pending_confirmation[0]
            statement = S.CONFIRMATION_STATEMENTS[key][lang.value]
            prompt = S.CONFIRMATION_TEMPLATE[lang.value].format(statement=statement)
            return AgentAction(ActionKind.CONFIRM_FACT, prompt, lang, slot_key=key,
                               rationale="extracted from the narrative; must be "
                                         "confirmed before it counts in full")

        for slot in S.SLOTS:
            if slot.key not in state.asked_slots:
                return AgentAction(ActionKind.ASK_SLOT, slot.prompt(lang), lang,
                                   slot_key=slot.key,
                                   rationale=f"risk factor not yet established "
                                             f"(priority {slot.priority})")

        screener = self._next_screener(state)
        if screener is not None:
            return screener

        return AgentAction(ActionKind.CLOSE, S.CLOSING_PROMPT[lang.value], lang,
                           rationale="all required questions administered")

    def _next_screener(self, state: IntakeState) -> Optional[AgentAction]:
        lang = state.language

        if len(state.ptsd) < len(S.PC_PTSD5):
            return self._screener_action(state, S.PC_PTSD5[len(state.ptsd)],
                                         len(state.ptsd), "trauma screen")

        target_phq = self._phq_target(state)
        if len(state.phq) < target_phq:
            return self._screener_action(state, S.PHQ_ITEMS[len(state.phq)],
                                         len(state.phq),
                                         "depression screen"
                                         + (" (escalated from the 2-item stem)"
                                            if target_phq > self.PHQ_STEM else ""))

        target_gad = self._gad_target(state)
        if len(state.gad) < target_gad:
            return self._screener_action(state, S.GAD_ITEMS[len(state.gad)],
                                         len(state.gad),
                                         "anxiety screen"
                                         + (" (escalated from the 2-item stem)"
                                            if target_gad > self.GAD_STEM else ""))

        # Rule 2 — always, regardless of everything above.
        if not self._cssrs_complete(state):
            index = len(state.cssrs_answers)
            return self._screener_action(state, S.CSSRS_ITEMS[index], index,
                                         "mandatory suicide screener; administered "
                                         "in every interaction without exception")

        if state.impairment is None:
            return self._screener_action(state, S.IMPAIRMENT_ITEM, 0,
                                         "functional impairment")
        return None

    def _screener_action(self, state: IntakeState, item: S.ScreenerItem,
                         index: int, rationale: str) -> AgentAction:
        return AgentAction(ActionKind.ASK_SCREENER, item.prompt(state.language),
                           state.language, instrument=item.instrument,
                           item_index=index, scale=item.scale, rationale=rationale)

    def _phq_target(self, state: IntakeState) -> int:
        if len(state.phq) >= self.PHQ_STEM and sum(state.phq[:2]) >= self.PHQ_ESCALATE_AT:
            return len(S.PHQ_ITEMS)
        return self.PHQ_STEM

    def _gad_target(self, state: IntakeState) -> int:
        if len(state.gad) >= self.GAD_STEM and sum(state.gad[:2]) >= self.GAD_ESCALATE_AT:
            return len(S.GAD_ITEMS)
        return self.GAD_STEM

    @staticmethod
    def _cssrs_complete(state: IntakeState) -> bool:
        return len(state.cssrs_answers) >= len(S.CSSRS_ITEMS)

    # ------------------------------------------------------------------

    def coverage_report(self, state: IntakeState) -> Dict[str, object]:
        """What the console shows about how complete this assessment is."""
        facts = state.to_context_facts()
        screeners = state.to_screeners()
        return {
            "phase": state.phase.value,
            "context_coverage": round(facts.coverage(), 3),
            "screening_coverage": round(screeners.coverage(), 3),
            "cssrs_administered": screeners.cssrs.administered,
            "slots_asked": len(state.asked_slots),
            "slots_total": len(S.SLOTS),
            "pending_confirmations": list(state.pending_confirmation),
            "crisis_flag": state.crisis_flag,
        }


def _set_at(target: List, index: int, value) -> None:
    while len(target) <= index:
        target.append(0 if isinstance(value, int) else False)
    target[index] = value
