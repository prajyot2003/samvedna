import type { NextAction } from "../types";

/**
 * The question the agent suggests, and why.
 *
 * The rationale is shown, not hidden behind a tooltip: a prompt with no stated
 * reason trains counsellors to click through without reading. The counsellor
 * asks the question — this panel never speaks to the caller.
 */
export function NextQuestion({
  action, onSlot, onScreener, onConsent, busy,
}: {
  action: NextAction | null;
  onSlot: (key: string, present: boolean) => void;
  onScreener: (instrument: string, index: number, value: number) => void;
  onConsent: (scope: string, decision: string) => void;
  busy: boolean;
}) {

  if (!action) {
    return <section className="card next-question closed">
      <span className="lbl">Interview complete</span>
    </section>;
  }

  const crisis = action.rationale.includes("crisis")
    || action.rationale.includes("without exception");

  return (
    <section className={`card next-question${crisis ? " crisis" : ""}`}>
      <span className="lbl">Ask the caller</span>
      <p className="prompt deva">{action.prompt}</p>
      <p className="rationale">{action.rationale}</p>

      {action.kind === "ask_consent" && (
        <div className="answers">
          <button className="primary" disabled={busy}
                  onClick={() => onConsent(action.scope!, "granted")}>
            Consent given
          </button>
          <button disabled={busy}
                  onClick={() => onConsent(action.scope!, "declined")}>
            Declined
          </button>
        </div>
      )}

      {action.kind === "ask_slot" && (
        <div className="answers">
          <button className="primary" disabled={busy}
                  onClick={() => onSlot(action.slot_key!, true)}>Yes</button>
          <button disabled={busy}
                  onClick={() => onSlot(action.slot_key!, false)}>No</button>
        </div>
      )}

      {action.kind === "confirm_fact" && (
        <div className="answers">
          <button className="primary" disabled={busy}
                  onClick={() => onSlot(action.slot_key!, true)}>
            Caller confirms
          </button>
          <button disabled={busy}
                  onClick={() => onSlot(action.slot_key!, false)}>
            Caller corrects this
          </button>
        </div>
      )}

      {action.kind === "ask_screener" && (
        <div className="answers scale">
          {(action.scale === "yes-no" ? [["No", 0], ["Yes", 1]] as const
            : action.scale === "0-4" ? [["None", 0], ["Mild", 1], ["Moderate", 2],
                                        ["Severe", 3], ["Extreme", 4]] as const
            : [["Not at all", 0], ["Several days", 1],
               ["More than half", 2], ["Nearly every day", 3]] as const
          ).map(([text, value]) => (
            <button key={value} disabled={busy}
                    onClick={() => onScreener(action.instrument!,
                                              action.item_index!, value)}>
              {text}
            </button>
          ))}
        </div>
      )}

      {action.kind === "open_narrative" && (
        <p className="hint">
          Write what the caller says in the box below — typed, or dictated.
        </p>
      )}

      {action.kind === "crisis_handover" && (
        <div className="answers">
          <button className="primary" disabled>
            Transfer in progress — stay on the line
          </button>
        </div>
      )}
    </section>
  );
}
