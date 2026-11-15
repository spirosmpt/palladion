import { useState } from "react";

/** Copies text for the operator to paste (radio log, SMS, WhatsApp). Sending stays manual. */
export function CopyButton({ text, label, el }: { text: string; label: string; el: boolean }) {
  const [done, setDone] = useState<"ok" | "fail" | null>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone("ok");
    } catch {
      setDone("fail");          // a page without clipboard access: the text is on screen to select
    }
    window.setTimeout(() => setDone(null), 2500);
  };
  return (
    <button className="link" onClick={() => void copy()}>
      {done === "ok" ? (el ? "Αντιγράφηκε ✓" : "Copied ✓")
        : done === "fail" ? (el ? "Δεν επιτρέπεται· επίλεξε το κείμενο" : "Not allowed; select the text")
        : label}
    </button>
  );
}
