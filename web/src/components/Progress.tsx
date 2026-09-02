import type { Coverage } from "../types";

/**
 * How far through the interview, and what is still missing.
 *
 * The C-SSRS is called out separately rather than folded into a percentage,
 * because an interview that is 90% complete without it is not 90% done — the
 * safety layer will hold the tier up until it is administered, and a counsellor
 * should know that before they wonder why the score will not settle.
 */
export function Progress({ coverage }: { coverage: Coverage }) {
  const asked = coverage.slots_asked;
  const total = coverage.slots_total;
  const slots = total ? asked / total : 0;
  const screening = coverage.screening_coverage;
  const overall = Math.round(((slots + screening) / 2) * 100);

  return (
    <section className="card progress">
      <div className="progress-head">
        <span className="lbl">Interview</span>
        <span className="num pct">{overall}%</span>
      </div>

      <div className="bars">
        <div>
          <div className="bar-label">
            <span>Risk questions</span>
            <span className="num">{asked}/{total}</span>
          </div>
          <div className="track"><div className="fill"
               style={{ width: `${slots * 100}%` }} /></div>
        </div>
        <div>
          <div className="bar-label">
            <span>Screening</span>
            <span className="num">{Math.round(screening * 100)}%</span>
          </div>
          <div className="track"><div className="fill"
               style={{ width: `${screening * 100}%` }} /></div>
        </div>
      </div>

      {!coverage.cssrs_administered && (
        <p className="progress-note warn">
          Suicide screener not yet administered. The assessment will not settle
          below High until it is.
        </p>
      )}
      {coverage.pending_confirmations.length > 0 && (
        <p className="progress-note">
          {coverage.pending_confirmations.length} fact
          {coverage.pending_confirmations.length > 1 ? "s" : ""} awaiting the
          caller's confirmation.
        </p>
      )}
    </section>
  );
}
