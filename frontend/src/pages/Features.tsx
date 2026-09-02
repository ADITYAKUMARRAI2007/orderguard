import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { Eyebrow } from "@/components/Eyebrow";
import { StaggerHeading } from "@/components/StaggerHeading";

export function Features() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api.featureMatrix().then(setData);
  }, []);

  if (!data?.generated_at) {
    return <p className="text-sm text-muted-foreground">{data?.note ?? "Loading…"}</p>;
  }

  const byCategory: Record<string, any[]> = {};
  for (const f of data.features) (byCategory[f.category] ??= []).push(f);

  return (
    <div className="flex flex-col gap-8 max-w-4xl">
      <div>
        <Eyebrow>System / Features</Eyebrow>
        <StaggerHeading
          as="h1" text="What is actually shipped, cited."
          accent={["shipped,"]}
          className="text-3xl md:text-4xl leading-[1.15] mt-2 mb-3"
        />
        <p className="text-sm text-muted-foreground mt-2 max-w-xl">
          Reconstructed by walking the shipped code. Every entry names the file or function that
          implements it.
        </p>
      </div>

      {Object.entries(byCategory).map(([category, features]) => (
        <div key={category}>
          <h2 className="text-sm font-medium mb-3">
            {category} ({features.length})
          </h2>
          <div className="grid gap-3">
            {features.map((f) => (
              <Card key={f.name} className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium text-sm">{f.name}</span>
                  <Badge variant={f.status === "shipped" ? "default" : "secondary"}>{f.status}</Badge>
                </div>
                <p className="text-xs text-muted-foreground mb-1">{f.description}</p>
                <code className="text-[10px] text-muted-foreground">{f.implemented_in}</code>
              </Card>
            ))}
          </div>
        </div>
      ))}
      <p className="text-xs text-muted-foreground">
        Generated {data.generated_at} · {data.features.length} features total
      </p>
    </div>
  );
}
