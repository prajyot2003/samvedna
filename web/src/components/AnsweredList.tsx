import type { AnsweredItem } from "../types";

/**
 * Every answer given so far, and a way to change one.
 *
 * Mis-clicks happen, and a caller correcting themselves mid-call is normal
 * rather than exceptional — "no, sorry, he does still live there". Without this
 * the only remedy was to abandon the interaction.
 *
 * A correction is a re-answer, not an erasure. The original answer stays in the
 * audit ledger and the new one is appended after it, so the record shows that a
 * counsellor changed their mind and when. Nothing here deletes anything.
 */
export function AnsweredList({
  items, onCorrect, onClose,
}: {
  items: AnsweredItem[];
  onCorrect: (item: AnsweredItem) => void;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true"
         aria-label="Review answers" onClick={onClose}>
      <div className="card modal answered" onClick={(e) => e.stopPropagation()}>
        <span className="lbl">Answers so far — {items.length}</span>
        <p className="muted">
          Correcting an answer records the change. The original stays in the
          ledger; nothing is erased.
        </p>

        {items.length === 0 ? (
          <p className="muted">Nothing answered yet.</p>
        ) : (
          <ul>
            {items.map((item) => (
              <li key={item.id}>
                <div>
                  <b>{item.label}</b>
                  <span className="given">{item.answer}</span>
                </div>
                <button onClick={() => onCorrect(item)}>Change</button>
              </li>
            ))}
          </ul>
        )}

        <div className="modal-actions">
          <button className="primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
