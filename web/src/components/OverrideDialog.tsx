import { useState } from "react";
import type { Tier } from "../types";

const TIERS: Tier[] = ["LOW", "MODERATE", "HIGH", "CRITICAL"];
const MIN_REASON = 10;

/**
 * The counsellor overruling the system.
 *
 * Always available, and never possible without a stated reason — the server
 * refuses one under ten characters, and the form says so before the request is
 * made rather than after. An unexplained reversal of a risk assessment leaves a
 * record that looks considered and is not.
 */
export function OverrideDialog({
  current, onSubmit, onCancel, busy,
}: {
  current: Tier;
  onSubmit: (tier: Tier, reason: string) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [tier, setTier] = useState<Tier>(current);
  const [reason, setReason] = useState("");
  const short = reason.trim().length < MIN_REASON;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true"
         aria-label="Override the assessed tier">
      <div className="card modal">
        <span className="lbl">Override the assessment</span>
        <p className="muted">
          Your decision replaces the system's and is what enters the case record.
          It is logged against your operator ID with the reason you give.
        </p>

        <label className="lbl" htmlFor="tier">Tier</label>
        <select id="tier" value={tier}
                onChange={(e) => setTier(e.target.value as Tier)}>
          {TIERS.map((t) => (
            <option key={t} value={t}>
              {t}{t === current ? " (current)" : ""}
            </option>
          ))}
        </select>

        <label className="lbl" htmlFor="reason">Reason</label>
        <textarea id="reason" rows={3} value={reason}
                  placeholder="What did you observe that the assessment missed?"
                  onChange={(e) => setReason(e.target.value)} />
        <p className={`hint${short && reason.length > 0 ? " warn" : ""}`}>
          {short
            ? `At least ${MIN_REASON} characters — the reason has to explain the decision.`
            : "This is recorded permanently in the audit ledger."}
        </p>

        <div className="modal-actions">
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" disabled={busy || short}
                  onClick={() => onSubmit(tier, reason.trim())}>
            Record override
          </button>
        </div>
      </div>
    </div>
  );
}
