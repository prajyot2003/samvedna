import type { AnsweredItem } from "../types";

const SCALES: Record<string, [string, number][]> = {
  "yes-no": [["No", 0], ["Yes", 1]],
  "0-3": [["Not at all", 0], ["Several days", 1],
          ["More than half", 2], ["Nearly every day", 3]],
  "0-4": [["None", 0], ["Mild", 1], ["Moderate", 2], ["Severe", 3], ["Extreme", 4]],
};

export function CorrectionDialog({
  item, onSubmit, onCancel, busy,
}: {
  item: AnsweredItem;
  onSubmit: (item: AnsweredItem, value: number | boolean) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const options = item.kind === "slot"
    ? [["No", 0], ["Yes", 1]] as [string, number][]
    : SCALES[item.scale ?? "0-3"] ?? SCALES["0-3"];

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true"
         aria-label="Correct an answer">
      <div className="card modal">
        <span className="lbl">Change this answer</span>
        <p className="prompt-echo">{item.label}</p>
        <p className="muted">Currently recorded as <b>{item.answer}</b>.</p>
        <div className="answers scale">
          {options.map(([text, value]) => (
            <button key={value} disabled={busy}
                    onClick={() => onSubmit(item,
                      item.kind === "slot" ? value === 1 : value)}>
              {text}
            </button>
          ))}
        </div>
        <div className="modal-actions">
          <button onClick={onCancel} disabled={busy}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
