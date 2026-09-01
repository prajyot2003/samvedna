import { useEffect, useState } from "react";

/**
 * A permanent, unmissable statement that this instance is not a live service.
 *
 * The README, the readiness endpoint and the DPIA all say this system has not
 * been cleared for live calls. A deployed instance that looks operational
 * quietly contradicts all three, and the contradiction would be discovered by
 * whoever needed it least. The banner is not dismissible for the same reason.
 */
export function DemoBanner({ apiBase }: { apiBase: string }) {
  const [show, setShow] = useState(false);
  const [reachable, setReachable] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/health`)
      .then((r) => r.json())
      .then((body) => {
        if (cancelled) return;
        setShow(Boolean(body.demo_banner));
        setReachable(true);
      })
      .catch(() => { if (!cancelled) setReachable(false); });
    return () => { cancelled = true; };
  }, [apiBase]);

  if (reachable === false) {
    return (
      <div className="demo-banner unreachable" role="alert">
        <b>No backend.</b> The console cannot reach the triage service at{" "}
        <code>{apiBase}</code>. Nothing on this page is computed.
      </div>
    );
  }
  if (!show) return null;

  return (
    <div className="demo-banner" role="alert">
      <b>Demonstration instance — not a live service.</b> Do not enter real
      complainant information. This build reports itself as not cleared for live
      calls: its crisis lexicons have not been reviewed by a native speaker, and
      no recognition or triage accuracy has been measured.
    </div>
  );
}
