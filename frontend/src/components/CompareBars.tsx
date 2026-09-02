import { motion } from "framer-motion";

// The horizontal-bar comparison from getsolari.com's "Performance" section
// ("Solari 199ms / Kernel 778ms / Steel 867ms / Browserbase 2,888ms") —
// same construction: label, a bar whose width is proportional to value,
// the winning row painted in the accent color, the rest muted. Renders
// whatever data it's given; never invents a number.

export interface CompareDatum {
  label: string;
  value: number;
  displayValue: string;
  highlight?: boolean;
}

export function CompareBars({ data }: { data: CompareDatum[] }) {
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="flex flex-col gap-3">
      {data.map((d, i) => (
        <motion.div
          key={d.label}
          className="flex items-center gap-4"
          initial={{ opacity: 0, x: -14 }}
          whileInView={{ opacity: 1, x: 0 }}
          viewport={{ once: true, margin: "-40px" }}
          transition={{ duration: 0.4, delay: i * 0.06 }}
        >
          <span className={`w-28 shrink-0 label-micro truncate ${d.highlight ? "text-warn" : "text-muted-foreground"}`}>
            {d.label}
          </span>
          <div className="flex-1 h-2 bg-border/40">
            <motion.div
              className={d.highlight ? "h-full bg-warn" : "h-full bg-muted-foreground/50"}
              initial={{ width: 0 }}
              whileInView={{ width: `${(d.value / max) * 100}%` }}
              viewport={{ once: true, margin: "-40px" }}
              transition={{ duration: 0.7, delay: i * 0.06 + 0.1, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
          <span className={`w-24 shrink-0 text-right label-micro ${d.highlight ? "text-warn" : "text-muted-foreground"}`}>
            {d.displayValue}
          </span>
        </motion.div>
      ))}
    </div>
  );
}
