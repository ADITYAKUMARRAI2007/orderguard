import { motion, type Variants } from "framer-motion";

// Word-by-word scroll reveal — confirmed against getsolari.com's own
// computed DOM: every heading/paragraph word is its own <span>, animated in
// with a per-word stagger the first time it scrolls into view. Font is
// Inter at weight 400 (their section headings, not the hero's heavier
// "Inter Display"), never JetBrains Mono — that stays reserved for labels,
// nav, and data, matching the same split Solari itself uses.

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.032 } },
};

const word: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.16, 1, 0.3, 1] } },
};

const TAGS = { h1: motion.h1, h2: motion.h2, h3: motion.h3, p: motion.p } as const;

interface Props {
  text: string;
  accent?: string[];
  as?: keyof typeof TAGS;
  className?: string;
  /** Defaults to Inter (Solari's own section-heading font). Pass the
   *  JetBrains Mono stack to keep a brand-identity headline in its own
   *  bold-caps voice while still getting the same word-reveal motion. */
  fontFamily?: string;
}

export function StaggerHeading({ text, accent = [], as = "h2", className = "", fontFamily = "var(--font-heading)" }: Props) {
  const Tag = TAGS[as];
  const words = text.split(" ");
  const accentSet = new Set(accent.map((w) => w.toLowerCase()));

  return (
    <Tag
      className={className}
      style={{ fontFamily }}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: "-60px" }}
      variants={container}
    >
      {words.map((w, i) => (
        <motion.span
          key={i}
          variants={word}
          style={{ display: "inline-block" }}
          className={accentSet.has(w.toLowerCase().replace(/[.,]/g, "")) ? "text-warn" : undefined}
        >
          {w}
          {i < words.length - 1 ? " " : ""}
        </motion.span>
      ))}
    </Tag>
  );
}
