import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { api, type CiChecksResponse } from "@/lib/api";
import { Eyebrow } from "@/components/Eyebrow";
import { StaggerHeading } from "@/components/StaggerHeading";
import { CompareBars, type CompareDatum } from "@/components/CompareBars";
import { ChecksList } from "@/components/ChecksList";

interface Baseline {
  name: string;
  description: string;
  unsafe_acceptance_rate: number;
  valid_acceptance_rate: number;
  unsafe_acceptance_count: number;
  total_attacks: number;
  leaked_amount_paise: number;
  total_exposed_paise: number;
}

function rupees(paise: number): string {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(paise / 100);
}

function baselineBars(baselines: Baseline[]): CompareDatum[] {
  return baselines.map((b) => ({
    label: b.name,
    value: b.unsafe_acceptance_rate * 100,
    displayValue: `${Math.round(b.unsafe_acceptance_rate * 100)}% unsafe`,
    highlight: b.name === "orderguard",
  }));
}

export function EvalPage() {
  const [bench, setBench] = useState<any>(null);
  const [judge, setJudge] = useState<any>(null);
  const [checks, setChecks] = useState<CiChecksResponse | null>(null);

  useEffect(() => {
    api.evalResults().then(setBench);
    api.judgeResults().then(setJudge);
    api.ciChecks().then(setChecks);
  }, []);

  return (
    <div className="flex flex-col gap-14 max-w-4xl">
      <div>
        <Eyebrow>Eval / Judge</Eyebrow>
        <StaggerHeading
          as="h1" text="An independent read, not our own."
          accent={["independent"]}
          className="text-3xl md:text-4xl leading-[1.15] mt-2 mb-3"
        />
        <p className="text-sm text-muted-foreground max-w-xl">
          Every real evidence artifact this repo writes, in one checks run — the same shape a CI
          run shows, because these numbers ARE what CI would report; there's no separate suite
          hiding behind them. Nothing below is computed for this page — every row is read from a
          file some other real command already wrote.
        </p>
      </div>

      {checks && (
        <div>
          <Eyebrow>CHECKS</Eyebrow>
          <StaggerHeading
            as="h2" text="Every claim, checkable."
            accent={["checkable."]}
            className="text-2xl md:text-3xl leading-[1.2] mt-2 mb-4"
          />
          <ChecksList data={checks} />
        </div>
      )}

      {bench?.baselines && (
        <div>
          <Eyebrow>PERFORMANCE</Eyebrow>
          <StaggerHeading
            as="h2" text="Independent re-verification, not a strawman."
            accent={["Independent"]}
            className="text-2xl md:text-3xl leading-[1.2] mt-2 mb-2"
          />
          <p className="text-sm text-muted-foreground mb-6 max-w-xl">
            Same fixed-fifty adversarial scenario set, three configurations. Unsafe acceptance rate —
            how often a corrupted cart was wrongly allowed to pay.
          </p>
          <Card className="p-5 max-w-xl">
            <CompareBars data={baselineBars(bench.baselines)} />
            <p className="text-xs text-muted-foreground mt-4">
              <code>no_guard</code> and <code>confirm_only</code> score identically — not a coincidence.
              Every attack tampers with what the merchant actually has, not with what the agent believes
              it asked for. Confirming an unverified belief does not verify it.
            </p>
          </Card>
          <Card className="p-5 max-w-xl mt-4">
            <h3 className="text-sm font-medium mb-3">Financial leakage — same scenario set, real ₹ amounts</h3>
            <div className="flex flex-col gap-2">
              {bench.baselines.map((b: Baseline) => (
                <div key={b.name} className="flex items-center justify-between text-sm">
                  <span className={b.name === "orderguard" ? "font-bold" : "text-muted-foreground"}>{b.name}</span>
                  <span className={b.leaked_amount_paise > 0 ? "text-destructive font-bold" : "text-signal font-bold"}>
                    {rupees(b.leaked_amount_paise)} <span className="text-muted-foreground font-normal">of {rupees(b.total_exposed_paise)} exposed</span>
                  </span>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-3">
              For every corrupted cart in the fixed-fifty set, the real paise total that cart would
              have charged if it had been allowed to pay — summed only for the carts each configuration
              actually would have let through.
            </p>
          </Card>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card className="p-5">
          <h2 className="text-sm font-medium mb-3">Benchmark summary</h2>
          {bench?.generated_at ? (
            <div className="flex flex-col gap-2 text-sm">
              <div>False-match (fixed 50): {Math.round(bench.fixed_fifty.false_match_rate * 100)}%</div>
              <div>False-match (hostile lab, {bench.attack_lab.total} scenarios): {Math.round(bench.attack_lab.false_match_rate * 100)}%</div>
              <p className="text-xs text-muted-foreground mt-2">Generated {bench.generated_at}</p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{bench?.note ?? "Loading…"}</p>
          )}
        </Card>
        <Card className="p-5">
          <h2 className="text-sm font-medium mb-3">Neutral judge report</h2>
          <p className="text-sm text-muted-foreground">{judge?.generated_at ? judge.summary : judge?.note ?? "Loading…"}</p>
        </Card>
      </div>
    </div>
  );
}
