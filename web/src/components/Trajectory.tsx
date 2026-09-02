import type { HistoryPoint, Tier } from "../types";

const BANDS: { tier: Tier; from: number; to: number }[] = [
  { tier: "LOW", from: 0, to: 25 },
  { tier: "MODERATE", from: 25, to: 50 },
  { tier: "HIGH", from: 50, to: 75 },
  { tier: "CRITICAL", from: 75, to: 100 },
];

const W = 100;
const H = 34;

/**
 * How the assessment moved during the call.
 *
 * A caller who becomes more distressed as they describe what happened looks
 * nothing like one who was distressed from the first word, and a single number
 * cannot tell them apart. Snapshots have been append-only since the schema was
 * written for exactly this; drawing them costs a sparkline.
 *
 * Tier bands sit behind the line so a rise is read against what it crossed,
 * not against an arbitrary scale, and points where a safety rule fired are
 * marked — those are the moments where the score stopped being the reason.
 */
export function Trajectory({ points }: { points: HistoryPoint[] }) {
  if (points.length < 2) return null;

  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (score: number) => H - (Math.max(0, Math.min(100, score)) / 100) * H;

  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(p.score).toFixed(2)}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const last = points[points.length - 1];
  const first = points[0];
  const delta = last.score - first.score;

  return (
    <div className="trajectory">
      <div className="trajectory-head">
        <span className="lbl">During this call</span>
        <span className={`num delta ${delta > 0.5 ? "up" : delta < -0.5 ? "down" : ""}`}>
          {delta > 0.5 ? "↑" : delta < -0.5 ? "↓" : "→"}{" "}
          {Math.abs(delta).toFixed(0)} points over {points.length} updates
        </span>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
           role="img" className="spark"
           aria-label={`Assessment moved from ${first.score.toFixed(0)} to ${last.score.toFixed(0)} across ${points.length} updates`}>
        {BANDS.map((b) => (
          <rect key={b.tier} x="0" y={y(b.to)} width={W} height={y(b.from) - y(b.to)}
                className={`band tier-${b.tier.toLowerCase()}`} />
        ))}
        <path d={area} className="spark-area" />
        <path d={line} className="spark-line" vectorEffect="non-scaling-stroke" />
        {points.map((p, i) => (
          p.model_bypassed ? (
            <circle key={i} cx={x(i)} cy={y(p.score)} r="1.6" className="spark-rule"
                    vectorEffect="non-scaling-stroke" />
          ) : null
        ))}
        <circle cx={x(points.length - 1)} cy={y(last.score)} r="1.8"
                className="spark-end" vectorEffect="non-scaling-stroke" />
      </svg>

      {points.some((p) => p.model_bypassed) && (
        <p className="trajectory-note">
          Marked points are where a safety rule set the tier, not the score.
        </p>
      )}
    </div>
  );
}
