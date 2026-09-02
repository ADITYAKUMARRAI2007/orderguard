import { useMemo } from "react";

// Cinematic backdrop for the hero — a starfield over a warm planetary
// horizon glow, in the style of getsolari.com's hero. Pure CSS, no image
// asset borrowed from that site (their photography is theirs); this
// reconstructs the same composition — dark sky, scattered stars, a glowing
// curved horizon — using our own existing --warn amber, which already sits
// within a few RGB points of Solari's accent (#F5B301 vs our #ffb000), so
// nothing here contradicts what --warn already means elsewhere (settlement /
// money-adjacent). Sits behind PipelineScene's transparent WebGL canvas —
// that canvas already renders with alpha:true for exactly this purpose.

interface Star {
  id: number;
  x: number;
  y: number;
  size: number;
  opacity: number;
}

function generateStars(count: number): Star[] {
  // A small deterministic LCG, not Math.random() — the field should look
  // identical on every render/reload rather than reshuffling, the same way
  // the rest of this hero (the diagram layout) is fixed, not randomized.
  let seed = 1337;
  const next = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  return Array.from({ length: count }, (_, id) => {
    const roll = next();
    return {
      id,
      x: next() * 100,
      y: next() * 62, // upper ~2/3 — leaves the glow's own space near the horizon clear
      size: roll < 0.82 ? 1 : roll < 0.96 ? 1.6 : 2.2,
      opacity: 0.2 + next() * 0.55,
    };
  });
}

export function SpaceHorizon() {
  const stars = useMemo(() => generateStars(110), []);

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none" aria-hidden="true">
      <div className="absolute inset-0">
        {stars.map((s) => (
          <span
            key={s.id}
            className="absolute rounded-full bg-white"
            style={{ left: `${s.x}%`, top: `${s.y}%`, width: s.size, height: s.size, opacity: s.opacity }}
          />
        ))}
      </div>

      {/* Planet body: a giant circle mostly below the viewport, only its top
          arc visible — the classic CSS "horizon curve" construction. */}
      <div
        className="absolute left-1/2 -translate-x-1/2"
        style={{
          bottom: "-128%",
          width: "170%",
          aspectRatio: "1",
          borderRadius: "50%",
          background:
            "radial-gradient(circle at 50% 0%, color-mix(in srgb, var(--warn) 50%, transparent) 0%, color-mix(in srgb, var(--warn) 16%, transparent) 20%, transparent 42%)",
        }}
      />
      {/* Rim light along the horizon itself. */}
      <div
        className="absolute left-1/2 -translate-x-1/2"
        style={{
          bottom: "-128%",
          width: "170%",
          aspectRatio: "1",
          borderRadius: "50%",
          border: "1px solid color-mix(in srgb, var(--warn) 55%, transparent)",
          boxShadow: "0 0 70px 8px color-mix(in srgb, var(--warn) 40%, transparent)",
        }}
      />
    </div>
  );
}
