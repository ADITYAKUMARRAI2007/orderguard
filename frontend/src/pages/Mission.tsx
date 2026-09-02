import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { PipelineCanvas } from "@/components/pipeline/PipelineCanvas";
import { PipelineScene } from "@/components/PipelineScene";
import { api, ApiError, type MissionStep } from "@/lib/api";

interface ChatTurn {
  who: "user" | "system";
  text: string;
  error?: boolean;
}

// Real Web Speech API — no mock. Absent on browsers without support, in
// which case the control simply never renders rather than pretending.
const SpeechRecognitionCtor: any =
  (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

const PRESETS = [
  "order milk from instamart",
  "check my github issues",
  "order dinner and check my github issues",
];

// Mirrors agent/missions.py::decompose_intents so the canvas can show
// pending intents while the real call is still in flight. Display only —
// the server's own decomposition is authoritative for what actually runs.
const MODIFIER_STARTS = ["for ", "to ", "at ", "in ", "with ", "under ", "by ", "using ", "via ", "from "];

function categoryOf(clause: string): string {
  const l = clause.toLowerCase();
  if (/github|issue|pull request|repo/.test(l)) return "DEV_TASK";
  if (/dinner|lunch|pizza|food|restaurant/.test(l)) return "COMMERCE_FOOD";
  if (/grocery|groceries|milk|instamart|vegetable/.test(l)) return "COMMERCE_GROCERY";
  return "COMMERCE_GENERAL";
}

function previewCategories(message: string): string[] {
  const rawClauses = message.split(/\band\b|\bthen\b|,/i).map((c) => c.trim()).filter(Boolean);
  const clauses = rawClauses.length ? rawClauses : [message];

  // A clause opening with a preposition and matching no keyword of its own
  // reads as a trailing modifier ("...and for work address"), not a second
  // independent request — fold it back rather than preview a bogus extra
  // intent. Same rule as the real backend decomposer, so the preview never
  // shows something the server would never actually produce.
  const merged: string[] = [];
  for (const raw of clauses) {
    const isModifier = categoryOf(raw) === "COMMERCE_GENERAL" && MODIFIER_STARTS.some((p) => raw.toLowerCase().startsWith(p));
    if (merged.length && isModifier) {
      merged[merged.length - 1] = `${merged[merged.length - 1]} and ${raw}`;
    } else {
      merged.push(raw);
    }
  }
  return merged.map(categoryOf);
}

export function Mission() {
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([
    { who: "system", text: "Ready. Describe what you need — I route it, you authorize anything that costs money." },
  ]);
  // Accumulates across the whole tab session — never cleared. A follow-up
  // reply APPENDS its step(s) here instead of replacing the array, so the
  // pipeline trace keeps everything already shown and only the new step
  // animates in. Wiping this on every send() (the old behaviour) is exactly
  // why answering a clarifying question looked like the whole mission
  // restarting from node zero: real backend continuity, fake visual reset.
  const [steps, setSteps] = useState<MissionStep[]>([]);
  const [runtimeName, setRuntimeName] = useState("");
  const [running, setRunning] = useState(false);
  const [pending, setPending] = useState<string[]>([]);
  const [listening, setListening] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const recognitionRef = useRef<any>(null);

  // Stable for the lifetime of this tab — pairs with app.py's
  // _CONVERSATION_SESSIONS, keyed (session_id, category), so a follow-up
  // reaches the same open conversation instead of a fresh, memoryless one.
  const sessionIdRef = useRef<string>(crypto.randomUUID());
  // ALL categories currently waiting on a reply — a multi-intent mission
  // ("order dinner and milk") can pause on two at once. This used to be a
  // single string, overwritten by whichever step a loop saw last, which
  // silently routed a reply meant for one paused intent into the OTHER
  // intent's conversation — the exact crossed-wires bug this fixes.
  const [pausedCategories, setPausedCategories] = useState<string[]>([]);
  // Which ONE of those the next message answers. Auto-set when there's
  // exactly one paused category (same zero-friction behaviour as before);
  // left null, forcing an explicit pick via the chips below, whenever more
  // than one is paused — never guessed from message content.
  const [replyTarget, setReplyTarget] = useState<string | null>(null);

  // A live counter while a request is in flight — a real LLM tool-use turn
  // routinely takes 10-20s, and a bare "EXECUTING…" with no elapsed time
  // reads as stuck rather than working.
  useEffect(() => {
    if (!running) return;
    const startedAt = Date.now();
    setElapsedMs(0);
    const id = setInterval(() => setElapsedMs(Date.now() - startedAt), 200);
    return () => clearInterval(id);
  }, [running]);

  async function send(text: string) {
    if (!text.trim() || running) return;
    const continuing = replyTarget;
    setTurns((t) => [...t, { who: "user", text }]);
    setInput("");
    setPending(continuing ? [continuing] : previewCategories(text));
    setRunning(true);
    setReplyTarget(null);
    try {
      const result = await api.runMission(text, sessionIdRef.current, continuing);
      setRuntimeName(result.runtime);
      // Continuing a paused step resolves IN PLACE — the node that was
      // waiting for this reply becomes the answer, rather than a duplicate
      // node appearing after it. A genuinely new, unrelated request appends.
      setSteps((prev) => {
        if (continuing) {
          const pausedIndex = prev.map((s) => s.category).lastIndexOf(continuing);
          if (pausedIndex !== -1) {
            return [...prev.slice(0, pausedIndex), ...result.steps, ...prev.slice(pausedIndex + 1)];
          }
        }
        return [...prev, ...result.steps];
      });
      // The category we just answered is no longer paused UNLESS this same
      // reply immediately raised another question for it (a multi-turn
      // clarification chain on the same intent) — every OTHER category that
      // was already paused stays paused, untouched by this turn.
      const stillPaused = continuing ? pausedCategories.filter((c) => c !== continuing) : [...pausedCategories];
      const lines = result.steps.map((s) => {
        const route = s.connector_id ? `${s.category} → ${s.connector_id}` : `${s.category} → no eligible connector`;
        const timing = s.duration_ms ? ` (${(s.duration_ms / 1000).toFixed(1)}s)` : "";
        // A step can genuinely succeed with zero tool-call results — the
        // model made no call, or asked a clarifying question instead. That
        // must never look identical to "nothing happened": show what it
        // actually said.
        const waitingForReply = s.results.length === 0 && s.model_text;
        const note = waitingForReply ? ` — "${s.model_text}"` : "";
        if (waitingForReply && !stillPaused.includes(s.category)) stillPaused.push(s.category);
        return `${route}${timing}${note}`;
      });
      setPausedCategories(stillPaused);
      // Only auto-target when there's exactly one live question — with two
      // or more, the next reply's target must be an explicit click, not a
      // guess (see the chips below the input).
      setReplyTarget(stillPaused.length === 1 ? stillPaused[0] : null);
      setTurns((t) => [...t, { who: "system", text: lines.join("\n") || "No intents resolved." }]);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setTurns((t) => [
        ...t,
        { who: "system", error: true, text: `Halted before changing anything: ${message}` },
      ]);
    } finally {
      setRunning(false);
      setPending([]);
    }
  }

  function toggleVoice() {
    if (!SpeechRecognitionCtor) return;
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const recognition = new SpeechRecognitionCtor();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.onresult = (e: any) => setInput(e.results[0][0].transcript);
    recognition.onend = () => setListening(false);
    recognition.onerror = () => setListening(false);
    recognitionRef.current = recognition;
    recognition.start();
    setListening(true);
  }

  return (
    <>
    <PipelineScene />
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(320px,400px)_1fr] gap-6 items-start mt-8">
      <section className="border-2 border-border bg-card hard" aria-label="Console">
        <header className="border-b-2 border-border px-4 py-2.5 flex items-center gap-2">
          <span className="label-micro text-signal">CONSOLE</span>
          <span className="ml-auto label-micro text-muted-foreground">
            {running ? `BUSY · ${(elapsedMs / 1000).toFixed(1)}s` : "IDLE"}
          </span>
        </header>

        <div className="px-4 py-4 flex flex-col gap-3 max-h-[300px] overflow-y-auto">
          <AnimatePresence initial={false}>
            {turns.map((turn, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: turn.who === "user" ? 10 : -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ type: "spring", stiffness: 300, damping: 26 }}
                className="text-[13px] leading-relaxed"
              >
                <span
                  className={`label-micro mr-2 ${
                    turn.who === "user" ? "text-foreground" : turn.error ? "text-destructive" : "text-signal"
                  }`}
                >
                  {turn.who === "user" ? "YOU ›" : turn.error ? "HALT ›" : "OG ›"}
                </span>
                <span className={`whitespace-pre-line ${turn.error ? "text-destructive" : "text-muted-foreground"}`}>{turn.text}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>

        <div className="border-t-2 border-border px-4 py-3">
          <label htmlFor="mission-input" className="label-micro text-muted-foreground mb-2 flex items-center gap-2 flex-wrap">
            <span>INSTRUCTION</span>
            {pausedCategories.length === 1 && (
              <span className="text-signal">· REPLYING TO {pausedCategories[0]} — SAME CONVERSATION</span>
            )}
          </label>
          {pausedCategories.length > 1 && (
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <span className="label-micro text-warn">
                {pausedCategories.length} INTENTS WAITING — WHICH ONE IS THIS REPLY FOR?
              </span>
              {pausedCategories.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setReplyTarget(cat)}
                  className={`label-micro border-2 px-2 py-1 transition-colors duration-150 ${
                    replyTarget === cat
                      ? "border-signal text-signal bg-signal/10"
                      : "border-border text-muted-foreground hover:border-signal hover:text-signal"
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <textarea
              id="mission-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={2}
              placeholder="order dinner and check my github issues"
              className="w-full bg-background border-2 border-border px-3 py-2 text-[13px] resize-none focus:outline-none focus:border-signal transition-colors duration-150 placeholder:text-muted-foreground/50"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
            />
            <div className="flex items-center gap-2 mt-2">
              {SpeechRecognitionCtor && (
                <button
                  type="button"
                  onClick={toggleVoice}
                  aria-label={listening ? "Stop voice input" : "Start voice input"}
                  aria-pressed={listening}
                  className={`size-11 border-2 grid place-items-center cursor-pointer transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-signal ${
                    listening
                      ? "border-destructive text-destructive bg-destructive/10"
                      : "border-border hover:border-signal hover:text-signal"
                  }`}
                >
                  {listening ? (
                    <motion.span
                      className="block size-3 bg-destructive"
                      animate={{ opacity: [1, 0.3, 1] }}
                      transition={{ duration: 0.9, repeat: Infinity }}
                    />
                  ) : (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <rect x="9" y="2" width="6" height="12" rx="3" />
                      <path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3" />
                    </svg>
                  )}
                </button>
              )}
              <button
                type="submit"
                disabled={running || !input.trim()}
                className="flex-1 h-11 bg-signal text-background font-bold text-xs tracking-widest border-2 border-signal cursor-pointer transition-all duration-150 hover:hard-signal hover:-translate-x-0.5 hover:-translate-y-0.5 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:translate-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal"
              >
                {running ? `EXECUTING… ${(elapsedMs / 1000).toFixed(1)}s` : "EXECUTE →"}
              </button>
            </div>
          </form>

          <div className="flex flex-wrap gap-1.5 mt-3">
            {PRESETS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => {
                  setInput(p);
                  // A preset is a fresh, explicit new topic — never a reply
                  // to whatever conversation happened to be open. Any
                  // already-paused intents stay paused, waiting; this just
                  // stops the NEXT send from being misattributed to one.
                  setReplyTarget(null);
                }}
                className="label-micro border border-border px-2 py-1.5 text-muted-foreground hover:border-signal hover:text-signal cursor-pointer transition-colors duration-150"
              >
                {p}
              </button>
            ))}
          </div>
        </div>

        <footer className="border-t-2 border-border px-4 py-2.5">
          <p className="label-micro text-muted-foreground leading-relaxed">
            YOU STAY IN CONTROL — EVERY FINANCIAL ACTION NEEDS YOUR EXPLICIT APPROVAL
          </p>
        </footer>
      </section>

      <PipelineCanvas steps={steps} runtime={runtimeName} running={running} pendingCategories={pending} />
    </div>
    </>
  );
}
