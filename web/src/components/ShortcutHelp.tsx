const ROWS: [string, string][] = [
  ["Y", "Yes · consent given · caller confirms"],
  ["N", "No · declined · caller corrects"],
  ["0 – 4", "Screener scale, in the order shown"],
  ["Ctrl + Enter", "Record what is typed in the narrative box"],
  ["U", "Review and correct an earlier answer"],
  ["?", "Show or hide this list"],
  ["Esc", "Close whatever is open"],
];

/** Discoverable rather than documented: nobody reads a shortcut list that
 *  lives in a wiki, and a hint in the corner is how anyone finds it. */
export function ShortcutHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true"
         aria-label="Keyboard shortcuts" onClick={onClose}>
      <div className="card modal shortcuts" onClick={(e) => e.stopPropagation()}>
        <span className="lbl">Keyboard</span>
        <p className="muted">
          Nothing fires while you are typing, so recording what the caller said
          never answers a question by accident.
        </p>
        <table>
          <tbody>
            {ROWS.map(([key, meaning]) => (
              <tr key={key}>
                <td><kbd>{key}</kbd></td>
                <td>{meaning}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="modal-actions">
          <button className="primary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
