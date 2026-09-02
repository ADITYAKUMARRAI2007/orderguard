import { motion } from "framer-motion";

// The small uppercase label above every Solari section heading — Fragment
// Mono, wide tracking, fades in a beat before the heading itself.

export function Eyebrow({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <motion.div
      className={`eyebrow text-signal ${className}`}
      initial={{ opacity: 0, y: 8 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.35 }}
    >
      {children}
    </motion.div>
  );
}
