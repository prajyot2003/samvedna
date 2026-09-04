import type { SVI } from "../types";
import { TierChip } from "./TierChip";

const REASON_TEXT: Record<string, string> = {
  insufficient_context_coverage: "not enough of the risk questions have been asked",
  insufficient_screening_coverage: "not enough of the screening has been completed",
  low_signal_confidence: "the audio or transcript is not reliable enough",
};

function label(key: string): string {
  if (key.startsWith("offence:")) return `Offence — ${key.slice(8).replace(/_/g, " ")}`;
  if (key.startsWith("factor:")) return key.slice(7).replace(/_/g, " ");
  if (key.startsWith("screen:")) return `${key.slice(7)} screen`;
  if (key === "model:distress_signal") return "Speech and language indicators";
  return key;
}

/**
 * The score, and immediately beneath it the reason for the score.
 *
 * A number with no explanation trains a counsellor either to obey it or to
 * ignore it, and both are failures. The contribution list is the argument the
 * system is making, in the order it weights it.
 */
export function SVIPanel({ svi }: { svi: SVI | null }) {
  if (!svi) {
    return (
      <section className="card svi-panel empty">
        <span className="lbl">Assessment</span>
        <p className="muted">Nothing assessed yet.</p>
      </section>
    );
  }

  const contributions = Object.entries(svi.contributions)
    .sort((a, b) => b[1] - a[1]);
  const max = contributions.length ? contributions[0][1] : 1;

  return (
    <section className={`card svi-panel tier-${svi.tier.toLowerCase()}`}>
      <header>
        <div>
          <span className="lbl">Stress Vulnerability Index</span>
          <div className="score num">{svi.score.toFixed(0)}<span>/100</span></div>
        </div>
        <TierChip tier={svi.tier} large />
      </header>

      {svi.model_bypassed && (
        <p className="banner rule" role="status">
          <strong>Set by rule, not by score.</strong> The assessment was raised to{" "}
          {svi.tier.toLowerCase()} by a safety rule. No model was consulted for
          this decision.
          <span className="bases">
            {svi.rule_bases.map((b) => <span key={b}>{b}</span>)}
          </span>
        </p>
      )}

      {svi.abstained && (
        <p className="banner warn" role="status">
          <strong>Raised because the assessment is incomplete</strong> —{" "}
          {svi.abstention_reasons.map((r) => REASON_TEXT[r] ?? r).join("; ")}.
          Uncertainty raises the tier; it never lowers it.
        </p>
      )}

      {svi.coarse_domains.length > 0 && (
        <p className="banner warn">
          Screened with the short form only: {svi.coarse_domains.join(", ")}.
        </p>
      )}

      <div className="channels">
        <div><span className="lbl">Context</span>
          <b className="num">{(svi.channel_a * 100).toFixed(0)}</b></div>
        <div><span className="lbl">Screening</span>
          <b className="num">{(svi.channel_b * 100).toFixed(0)}</b></div>
        <div><span className="lbl">Speech signal</span>
          <b className="num">+{svi.channel_c_delta.toFixed(1)}</b></div>
      </div>

      <div className="why">
        <span className="lbl">Why this score</span>
        <ul>
          {contributions.map(([key, value]) => (
            <li key={key}>
              <span className="bar" style={{ width: `${(value / max) * 100}%` }} />
              <span className="k">{label(key)}</span>
              <span className="v num">{value.toFixed(1)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
