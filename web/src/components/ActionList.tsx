import type { Action } from "../types";

function due(action: Action): string {
  if (action.immediate) return "now";
  if (!action.due_at) return "—";
  const minutes = action.sla_minutes;
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} d`;
}

/**
 * The action packet — owner, deadline, and the provision it rests on.
 *
 * The statutory basis is displayed on every row rather than tucked into a
 * detail view. It is the difference between "please consider counselling" and
 * something a district officer can act on, and it is what a counsellor quotes
 * when the referral is questioned.
 */
export function ActionList({ actions }: { actions: Action[] }) {
  if (actions.length === 0) {
    return <section className="card actions empty">
      <span className="lbl">Actions</span>
      <p className="muted">No actions raised yet.</p>
    </section>;
  }
  return (
    <section className="card actions">
      <span className="lbl">Actions — {actions.length}</span>
      <ul>
        {actions.map((action) => (
          <li key={action.action_id} className={action.immediate ? "immediate" : ""}>
            <div className="row">
              <b>{action.label}</b>
              <span className={`due num${action.immediate ? " now" : ""}`}>
                {due(action)}
              </span>
            </div>
            <div className="meta">
              <span className="owner">{action.owner_label}</span>
              {action.statutory_basis && (
                <span className="basis">{action.statutory_basis}</span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
