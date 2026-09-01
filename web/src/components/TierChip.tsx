import type { Tier } from "../types";

const LABEL: Record<Tier, string> = {
  LOW: "Low", MODERATE: "Moderate", HIGH: "High", CRITICAL: "Critical",
};

/**
 * Tier is carried by shape as well as colour — a filled dot and a left stripe —
 * because roughly one man in twelve has a colour vision deficiency and a
 * helpline console is not a place to encode critical state in hue alone.
 */
export function TierChip({ tier, large = false }: { tier: Tier; large?: boolean }) {
  return (
    <span className={`tier-chip tier-${tier.toLowerCase()}${large ? " large" : ""}`}>
      <span className="dot" aria-hidden="true" />
      {LABEL[tier]}
    </span>
  );
}
