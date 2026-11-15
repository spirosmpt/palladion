/** "Leaves in 7″ · Undo": the one moment to take a send back. */
export function UndoBar({ pending, undo, el }: { pending: { label: string; left: number } | null; undo: () => void; el: boolean }) {
  if (!pending) return null;
  return (
    <div className="undo-bar" role="status" aria-live="polite">
      <span>{pending.label} {el ? `φεύγει σε ${pending.left}″` : `leaves in ${pending.left}″`}</span>
      <button className="undo-btn" onClick={undo}>{el ? "Αναίρεση" : "Undo"}</button>
      <span className="muted small">{el ? "Αν κλείσεις την κάρτα πριν, δεν στέλνεται." : "Close the card before then and it is not sent."}</span>
    </div>
  );
}
