import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { CiCheck, CiChecksResponse, CheckStatus } from "@/lib/api";

// GitHub Actions' own checks-run list, deliberately: a status banner, then
// one row per real check with a circular pass/fail icon, a summary, and an
// expandable detail panel — the exact shape a PR's "Checks" tab renders.
// Every row here is a real artifact this repo's own scripts wrote (see
// app.py::ci_checks) — nothing is a mock of what CI would say, because
// there IS no separate CI; these ARE the checks.

function StatusIcon({ status, size = 20 }: { status: CheckStatus; size?: number }) {
  if (status === "success") {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="8" className="fill-signal" />
        <path d="M4.5 8.2L6.8 10.5L11.5 5.5" stroke="var(--background)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      </svg>
    );
  }
  if (status === "failure") {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="8" className="fill-destructive" />
        <path d="M5 5L11 11M11 5L5 11" stroke="var(--background)" strokeWidth="1.6" strokeLinecap="round" fill="none" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="8" className="fill-muted-foreground" opacity="0.5" />
      <circle cx="8" cy="8" r="2.4" className="fill-background" />
    </svg>
  );
}

function CheckRow({ check }: { check: CiCheck }) {
  const [open, setOpen] = useState(false);
  const hasDetail = check.detail.length > 0;

  return (
    <div className="border-b border-border/60 last:border-0">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={`w-full flex items-center gap-3 px-4 py-3 text-left transition-colors duration-150 ${hasDetail ? "cursor-pointer hover:bg-secondary/40" : "cursor-default"}`}
        aria-expanded={open}
      >
        <StatusIcon status={check.status} />
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-medium truncate">{check.name}</div>
          <div className="text-xs text-muted-foreground truncate">{check.summary}</div>
        </div>
        {check.duration_s != null && (
          <span className="label-micro text-muted-foreground shrink-0">{check.duration_s.toFixed(1)}s</span>
        )}
        {hasDetail && (
          <motion.span
            className="shrink-0 text-muted-foreground"
            animate={{ rotate: open ? 90 : 0 }}
            transition={{ duration: 0.15 }}
          >
            ▸
          </motion.span>
        )}
      </button>
      <AnimatePresence initial={false}>
        {open && hasDetail && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-3 pl-11 flex flex-col gap-1">
              {check.detail.map((line, i) => (
                <div key={i} className="text-xs font-mono text-muted-foreground flex items-center gap-2">
                  <span className={check.status === "failure" ? "text-destructive" : "text-signal"}>›</span>
                  {line}
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ChecksList({ data }: { data: CiChecksResponse }) {
  const passCount = data.checks.filter((c) => c.status === "success").length;
  const allPassed = data.overall === "success";

  return (
    <div className="border-2 border-border bg-card overflow-hidden rounded-xl">
      <div className={`flex items-center gap-3 px-4 py-4 ${allPassed ? "bg-signal/10" : "bg-destructive/10"}`}>
        <StatusIcon status={data.overall} size={28} />
        <div className="flex-1">
          <div className="text-sm font-semibold">
            {allPassed ? "All checks have passed" : "Some checks were not successful"}
          </div>
          <div className="text-xs text-muted-foreground">
            {passCount} / {data.checks.length} checks passed
            {data.commit && <span className="font-mono"> · {data.commit.slice(0, 7)}</span>}
          </div>
        </div>
      </div>
      <div>
        {data.checks.map((check) => (
          <CheckRow key={check.name} check={check} />
        ))}
      </div>
    </div>
  );
}
