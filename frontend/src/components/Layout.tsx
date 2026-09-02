import { NavLink, Outlet, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Footer } from "./Footer";

const LINKS = [
  { to: "/", label: "MISSION", idx: "01" },
  { to: "/shop", label: "SHOP", idx: "02" },
  { to: "/connectors", label: "CONNECTORS", idx: "03" },
  { to: "/attack-lab", label: "ATTACK LAB", idx: "04" },
  { to: "/evidence", label: "EVIDENCE", idx: "05" },
  { to: "/features", label: "FEATURES", idx: "06" },
  { to: "/eval", label: "EVAL", idx: "07" },
];

export function Layout() {
  const location = useLocation();
  const [runtime, setRuntime] = useState<string>("…");
  const [chainOk, setChainOk] = useState<boolean | null>(null);

  useEffect(() => {
    api.runtimeStatus().then((s) => setRuntime(s.active_agent_runtime)).catch(() => setRuntime("offline"));
    api.auditVerify().then((a) => setChainOk(a.verified)).catch(() => setChainOk(null));
  }, [location.pathname]);

  return (
    <div className="dark min-h-screen bg-background text-foreground flex flex-col">
      {/* Top status strip — a real instrument reading, not chrome. */}
      <div className="border-b-2 border-border bg-card">
        <div className="mx-auto max-w-[1500px] px-5 h-8 flex items-center gap-5 label-micro text-muted-foreground">
          <span className="text-signal">◉ ORDERGUARD CONTROL PLANE</span>
          <span className="hidden sm:inline">RUNTIME: <span className="text-foreground">{runtime}</span></span>
          <span className="hidden md:inline">
            AUDIT CHAIN:{" "}
            <span className={chainOk === false ? "text-destructive" : "text-signal"}>
              {chainOk === null ? "…" : chainOk ? "INTACT" : "TAMPERED"}
            </span>
          </span>
          <span className="ml-auto hidden lg:inline">DETERMINISTIC CODE AUTHORIZES MONEY — NOT THE MODEL</span>
        </div>
      </div>

      <header className="border-b-2 border-border bg-background sticky top-0 z-50">
        <div className="mx-auto max-w-[1500px] px-5 flex items-stretch">
          <div className="flex items-center gap-3 pr-6 border-r-2 border-border py-4">
            <motion.span
              aria-hidden="true"
              className="grid place-items-center size-9 border-2 border-signal text-signal text-lg font-bold bg-signal/10"
              animate={{ opacity: [1, 0.45, 1] }}
              transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
            >
              ◎
            </motion.span>
            <span className="text-lg font-extrabold tracking-tighter">ORDERGUARD</span>
          </div>
          <nav className="flex items-stretch overflow-x-auto" aria-label="Sections">
            {LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === "/"}
                className={({ isActive }) =>
                  `relative px-5 py-4 flex items-center gap-2 border-r-2 border-border whitespace-nowrap transition-colors duration-150 cursor-pointer focus-visible:outline-2 focus-visible:outline-signal focus-visible:-outline-offset-2 ${
                    isActive ? "bg-signal text-background" : "hover:bg-secondary"
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <span className={`label-micro ${isActive ? "text-background/70" : "text-muted-foreground"}`}>
                      {link.idx}
                    </span>
                    <span className="text-xs font-bold tracking-wide">{link.label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-[1500px] px-5 py-7">
        <Outlet />
      </main>

      <Footer />
    </div>
  );
}
