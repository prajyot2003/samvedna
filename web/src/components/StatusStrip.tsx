import type { Coverage, InteractionState } from "../types";

function pct(value: number): string { return `${Math.round(value * 100)}%`; }

/**
 * Signal quality, coverage, and consent state — the three things that tell a
 * counsellor how much the assessment on screen is actually worth.
 */
export function StatusStrip({ state }: { state: InteractionState }) {
  const c: Coverage = state.coverage;
  const poor = state.signal.confidence === "low";

  return (
    <div className="status-strip">
      {state.passive_mode && (
        <span className="pill passive">
          Not analysed — caller declined. Full human handling.
        </span>
      )}
      <span className={`pill${poor ? " warn" : ""}`}>
        Signal {poor ? "unreliable" : "usable"}
        {poor && state.signal.reasons.length > 0 &&
          ` — ${state.signal.reasons.join(", ").replace(/_/g, " ")}`}
      </span>
      <span className="pill">
        Risk questions <b className="num">{c.slots_asked}/{c.slots_total}</b>
      </span>
      <span className="pill">
        Screening <b className="num">{pct(c.screening_coverage)}</b>
      </span>
      <span className={`pill${c.cssrs_administered ? "" : " warn"}`}>
        Suicide screen {c.cssrs_administered ? "done" : "not yet done"}
      </span>
      {c.crisis_flag && <span className="pill crisis">Crisis indicator</span>}
    </div>
  );
}
