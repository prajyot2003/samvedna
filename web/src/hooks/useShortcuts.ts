import { useEffect } from "react";

/**
 * Keyboard bindings for the live call screen.
 *
 * A counsellor using this has a phone in one hand and someone in distress on
 * the other end of it. Reaching for a mouse to answer "yes" costs attention
 * that belongs on the caller, and the interview is thirty-odd questions long.
 *
 * Two rules that keep this from being a hazard: nothing fires while focus is in
 * a text field, so typing what the caller said never triggers an answer; and
 * nothing destructive is bound, so a mistyped key can only record an answer
 * that is visible and correctable, never close a case or send a referral.
 */
export function useShortcuts(
  handlers: Record<string, () => void>,
  enabled = true,
) {
  useEffect(() => {
    if (!enabled) return;

    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select"
          || target?.isContentEditable) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      const handler = handlers[event.key.toLowerCase()];
      if (handler) {
        event.preventDefault();
        handler();
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handlers, enabled]);
}
