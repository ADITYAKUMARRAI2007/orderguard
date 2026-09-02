import { NavLink } from "react-router-dom";
import { StaggerHeading } from "./StaggerHeading";

// Solari's own footer shape: the hero line repeated, a CTA, then real link
// columns and a copyright row. Content is ours throughout — no placeholder
// Terms/Privacy/Blog links to pages that don't exist in this repo.

const COLUMNS: { title: string; links: { to: string; label: string }[] }[] = [
  {
    title: "Product",
    links: [
      { to: "/", label: "Mission" },
      { to: "/shop", label: "Shop" },
      { to: "/connectors", label: "Connectors" },
    ],
  },
  {
    title: "Proof",
    links: [
      { to: "/attack-lab", label: "Attack Lab" },
      { to: "/evidence", label: "Evidence" },
      { to: "/eval", label: "Eval" },
      { to: "/features", label: "Features" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="border-t-2 border-border bg-card mt-16">
      <div className="mx-auto max-w-[1500px] px-5 py-16 flex flex-col items-center text-center border-b-2 border-border">
        <StaggerHeading
          as="h2"
          text="Find. Verify. Pay. Prove."
          fontFamily="var(--font-sans)"
          className="text-3xl md:text-5xl font-extrabold tracking-tighter mb-6"
        />
        <NavLink
          to="/shop"
          className="label-micro border-2 border-signal bg-signal text-background px-5 py-3 hover:hard-signal transition-all duration-150"
        >
          START A REAL PURCHASE →
        </NavLink>
      </div>

      <div className="mx-auto max-w-[1500px] px-5 py-10 grid grid-cols-2 sm:grid-cols-4 gap-8">
        <div className="col-span-2 sm:col-span-1">
          <div className="flex items-center gap-2 mb-2">
            <span className="grid place-items-center size-6 border-2 border-signal text-signal text-xs font-bold bg-signal/10">◎</span>
            <span className="text-sm font-extrabold tracking-tighter">ORDERGUARD</span>
          </div>
          <p className="label-micro text-muted-foreground leading-relaxed">
            DETERMINISTIC CODE AUTHORIZES MONEY — NOT THE MODEL
          </p>
        </div>
        {COLUMNS.map((col) => (
          <div key={col.title}>
            <div className="label-micro text-muted-foreground mb-3">{col.title}</div>
            <div className="flex flex-col gap-2">
              {col.links.map((l) => (
                <NavLink key={l.to} to={l.to} className="text-[13px] text-foreground/80 hover:text-signal transition-colors duration-150 w-fit">
                  {l.label}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="mx-auto max-w-[1500px] px-5 py-4 border-t-2 border-border label-micro text-muted-foreground flex flex-wrap items-center gap-3">
        <span>© 2026 ORDERGUARD</span>
        <span className="ml-auto">RAZORPAY AI BUILDATHON · TRACK 01</span>
      </div>
    </footer>
  );
}
