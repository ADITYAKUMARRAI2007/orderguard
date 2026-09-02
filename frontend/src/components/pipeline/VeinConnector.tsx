import { useId } from "react";
import { motion } from "framer-motion";

// Organic curved connector between pipeline nodes — a glowing "vein" of
// light rather than a straight wire, built from three real SVG layers (a
// blurred halo, a bright core stroke, a travelling packet along the exact
// same curve) so the glow is actual rendered geometry, not a CSS filter
// slapped on a box. Stays inside the established signal/warn/destructive
// palette on purpose — a purple/blue gradient glow would read as generic
// "AI product" chrome, not this product's own brutalist-terminal language.

export type VeinStatus = "active" | "pending" | "blocked" | "idle";

const COLOR: Record<VeinStatus, string> = {
  active: "var(--signal)",
  pending: "var(--warn)",
  blocked: "var(--destructive)",
  idle: "var(--border)",
};

const D = "M2,16 C18,4 30,28 50,16 S82,4 98,16";

export function VeinConnector({ status }: { status: VeinStatus }) {
  const uid = useId().replace(/[:]/g, "");
  const color = COLOR[status];
  const lit = status === "active" || status === "pending" || status === "blocked";

  return (
    <div className="w-10 md:w-12 shrink-0 self-center relative h-8" aria-hidden="true">
      <svg viewBox="0 0 100 32" className="w-full h-full overflow-visible">
        <defs>
          <filter id={`glow-${uid}`} x="-60%" y="-300%" width="220%" height="700%">
            <feGaussianBlur stdDeviation="2.4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* halo */}
        <path
          d={D} fill="none" stroke={color} strokeWidth={lit ? 4 : 1.5}
          opacity={lit ? 0.32 : 0.12} filter={`url(#glow-${uid})`}
        />

        {/* core */}
        <motion.path
          d={D} fill="none" stroke={color} strokeWidth={1.3} strokeLinecap="round"
          initial={false}
          animate={{ opacity: lit ? 1 : 0.35 }}
          transition={{ duration: 0.4 }}
        />

        {/* arrowhead */}
        <path d="M93,11.5 L100,16 L93,20.5 Z" fill={color} opacity={lit ? 1 : 0.35} />

        {status === "active" && (
          <circle r="2" fill={color}>
            <animateMotion dur="1.15s" repeatCount="indefinite" path={D} />
          </circle>
        )}
      </svg>
    </div>
  );
}
