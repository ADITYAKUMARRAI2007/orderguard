# OrderGuard — frontend

The React product surface: Mission (natural-language agent flow), Shop (real
checkout against FreshCart), Connectors, Attack Lab, Evidence, Features, and
Eval. See the [repo root README](../README.md) for what OrderGuard is and
the security architecture this UI renders.

## Run

From the repo root:

```bash
make dev
```

starts this frontend (Vite, `:5173`) together with the backend (`:8000`).
To run just this frontend against an already-running backend:

```bash
npm install
npm run dev
```

## Stack

React + TypeScript + Vite, Tailwind, shadcn/ui components, Framer Motion for
the pipeline/mission animations, three.js for the Mission page's 3D pipeline
visualization. `src/lib/api.ts` and `src/lib/shop.ts` are the only files that
talk to the backend — every number rendered elsewhere is read from what
those return, never hardcoded in a component.
