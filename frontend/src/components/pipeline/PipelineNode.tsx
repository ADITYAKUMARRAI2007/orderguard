import { motion } from "framer-motion";

export type NodeStatus = "ok" | "blocked" | "idle" | "running" | "paused";

interface Props {
  kind: string;
  value: string;
  status: NodeStatus;
  index: number;
  meta?: string;
  wide?: boolean;
}

const BORDER: Record<NodeStatus, string> = {
  ok: "border-signal",
  blocked: "border-destructive",
  running: "border-warn",
  paused: "border-warn",
  idle: "border-border",
};

const SHADOW: Record<NodeStatus, string> = {
  ok: "hard-signal",
  blocked: "hard-destructive",
  running: "hard",
  paused: "hard",
  idle: "hard",
};

// "paused" gets its own glyph — a pause bar, not the "running" arrow — so a
// clarifying question reads as "waiting for you" rather than "still working"
// or "stuck/failed".
const GLYPH: Record<NodeStatus, string> = {
  ok: "✓",
  blocked: "✕",
  running: "▶",
  paused: "❚❚",
  idle: "·",
};

const TEXT: Record<NodeStatus, string> = {
  ok: "text-signal",
  blocked: "text-destructive",
  running: "text-warn",
  paused: "text-warn",
  idle: "text-muted-foreground",
};

// A blurred plane in the same color, sitting behind the hard-edged card —
// real layered depth (two actual painted surfaces, offset in z) rather
// than a CSS drop-shadow trick. Only lit stages get one: an idle/unused
// feature should read as flat and inert, not glow for no reason.
const GLOW: Record<NodeStatus, string | null> = {
  ok: "var(--signal)",
  blocked: "var(--destructive)",
  running: "var(--warn)",
  paused: "var(--warn)",
  idle: null,
};

export function PipelineNode({ kind, value, status, index, meta, wide }: Props) {
  const glow = GLOW[status];
  return (
    <motion.div
      variants={{
        hidden: { opacity: 0, x: -14, boxShadow: "0px 0px 0 0 transparent" },
        show: { opacity: 1, x: 0 },
      }}
      transition={{ type: "spring", stiffness: 260, damping: 24, delay: index * 0.06 }}
      whileHover={{ x: -2, y: -2 }}
      className="relative shrink-0"
      style={{ transformStyle: "preserve-3d" }}
    >
      {glow && (
        <motion.div
          aria-hidden="true"
          className="absolute inset-0 rounded-full pointer-events-none"
          style={{ background: glow, filter: "blur(18px)", transform: "translateZ(-24px)" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: [0.16, 0.3, 0.16] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        />
      )}
      <div
        className={`relative border-2 bg-card px-3.5 py-2.5 ${wide ? "min-w-[210px] max-w-[300px]" : "min-w-[132px] max-w-[210px]"} ${BORDER[status]} ${SHADOW[status]} ${status === "running" ? "scanning overflow-hidden" : ""}`}
      >
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <span className="label-micro text-muted-foreground">{kind}</span>
          <span className={`text-[11px] leading-none ${TEXT[status]}`} aria-hidden="true">
            {GLYPH[status]}
          </span>
        </div>
        <div className="text-[13px] font-bold leading-tight truncate" title={value}>
          {value}
        </div>
        {meta && <div className="label-micro text-muted-foreground mt-1.5 truncate" title={meta}>{meta}</div>}
      </div>
    </motion.div>
  );
}
