import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { DashboardSummary, Readiness, Tier } from "./types";
import { TierChip } from "./components/TierChip";

const TIERS: Tier[] = ["LOW", "MODERATE", "HIGH", "CRITICAL"];

function overdueBy(dueAt: string): string {
  const minutes = Math.round((Date.now() - new Date(dueAt).getTime()) / 60000);
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} d`;
}

/**
 * The district officer's screen.
 *
 * Two things are deliberately given the same prominence as the caseload: the
 * SLA breaches, because an entitlement that misses its statutory deadline is
 * the failure this system exists to prevent; and the readiness verdict, because
 * a deployment blocker that lives only in a report nobody opens is not a
 * blocker.
 */
export function DistrictDashboard() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [audit, setAudit] = useState<{ ok: boolean; summary: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([api.dashboard(), api.readiness()]);
      setSummary(s);
      setReadiness(r);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (error) return <div className="dashboard"><p className="error">{error}</p></div>;
  if (!summary || !readiness) {
    return <div className="dashboard"><p className="muted">Loading…</p></div>;
  }

  const total = TIERS.reduce((n, t) => n + (summary.tier_distribution[t] ?? 0), 0);

  return (
    <div className="dashboard">
      {!readiness.production_ready && (
        <section className="card readiness">
          <span className="lbl">Not cleared for live calls</span>
          <ul>{readiness.blockers.map((b) => <li key={b}>{b}</li>)}</ul>
        </section>
      )}

      <section className="tiles">
        {TIERS.map((tier) => (
          <div key={tier} className={`card tile tier-${tier.toLowerCase()}`}>
            <TierChip tier={tier} />
            <b className="num">{summary.tier_distribution[tier] ?? 0}</b>
            <span className="muted num">
              {total ? Math.round(((summary.tier_distribution[tier] ?? 0) / total) * 100) : 0}%
            </span>
          </div>
        ))}
        <div className="card tile">
          <span className="lbl">Live now</span>
          <b className="num">{summary.live_interactions}</b>
        </div>
      </section>

      <section className="card breaches">
        <span className="lbl">
          Deadlines missed — {summary.overdue_count}
        </span>
        {summary.overdue_actions.length === 0 ? (
          <p className="muted">Nothing overdue.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Action</th><th>Owner</th><th>Overdue by</th><th>Basis</th></tr>
            </thead>
            <tbody>
              {summary.overdue_actions.map((a, i) => (
                <tr key={`${a.interaction_id}-${a.action_id}-${i}`}>
                  <td>{a.label}</td>
                  <td>{a.owner}</td>
                  <td className="num late">{overdueBy(a.due_at)}</td>
                  <td className="basis">{a.basis || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card lexicons">
        <span className="lbl">Language coverage</span>
        <table>
          <thead>
            <tr><th>Language</th><th>Crisis terms</th><th>Reviewed</th><th>Version</th></tr>
          </thead>
          <tbody>
            {Object.entries(readiness.lexicons).map(([code, lex]) => (
              <tr key={code}>
                <td>{lex.name}</td>
                <td className="num">{lex.terms}</td>
                <td className={lex.reviewed ? "" : "late"}>
                  {lex.reviewed ? "yes" : "not yet"}
                </td>
                <td className="mono">{lex.version}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">
          Detection quality is not equal across languages. Where a lexicon is
          unreviewed, the abstention path is what protects those callers —
          assessments are raised rather than lowered when the system is unsure.
        </p>
      </section>

      <section className="card audit">
        <span className="lbl">Audit ledger</span>
        <div className="answers">
          <button onClick={() =>
            api.verifyAudit().then((r) => setAudit({ ok: r.ok, summary: r.summary }))
              .catch((e) => setAudit({ ok: false, summary: String(e) }))}>
            Verify the chain
          </button>
          {audit && (
            <span className={audit.ok ? "verdict ok" : "verdict bad"}>
              {audit.summary}
            </span>
          )}
        </div>
      </section>
    </div>
  );
}
