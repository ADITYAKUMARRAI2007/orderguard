import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { motion } from "framer-motion";
import { SpaceHorizon } from "./SpaceHorizon";
import { Eyebrow } from "./Eyebrow";
import { StaggerHeading } from "./StaggerHeading";

// Real WebGL, driven by Three.js directly rather than react-three-fiber.
// R3F 9.7 crashes on React 19.2.8 — its `its-fine` context bridge calls
// hooks outside a render pass ("Cannot read properties of null (reading
// 'useMemo')"), which left the canvas stuck at its default 300x150 and
// never fired onCreated. Driving Three.js from one effect removes that
// entire class of failure and costs us nothing here.
//
// Labels are real DOM in JetBrains Mono, projected from world space each
// frame — crisper than SDF text in WebGL and consistent with the rest of
// the interface.
//
// The geometry carries the product's argument: probabilistic stages sit on
// the near side of a trust boundary, deterministic verification beyond it,
// settlement furthest back. Depth is structural and always rendered; only
// the orbit and the travelling packets are motion, and only those are
// disabled under prefers-reduced-motion.

const SIGNAL = 0x00ff9c;
const WARN = 0xffb000;
const GREY = 0x8a8a82;
const BG = 0x0a0a0a;

type Plane = "agent" | "guard" | "money";

interface Stage {
  id: string;
  label: string;
  sub: string;
  pos: [number, number, number];
  plane: Plane;
}

const STAGES: Stage[] = [
  { id: "intent", label: "INTENT", sub: "NATURAL LANGUAGE", pos: [-7.4, 2.0, 3.6], plane: "agent" },
  { id: "route", label: "ROUTE", sub: "CAPABILITY MATCH", pos: [-4.5, -1.6, 2.0], plane: "agent" },
  { id: "connector", label: "CONNECTOR", sub: "ELIGIBILITY + AUTH", pos: [-1.6, 1.8, 0.4], plane: "agent" },
  { id: "reread", label: "RE-READ", sub: "AUTHORITATIVE FETCH", pos: [1.6, -1.8, -1.2], plane: "guard" },
  { id: "gates", label: "22 GATES", sub: "DETERMINISTIC", pos: [4.5, 1.6, -2.8], plane: "guard" },
  { id: "authorize", label: "AUTHORIZE", sub: "SIGNED · ED25519", pos: [7.4, -2.0, -4.4], plane: "money" },
];

const PLANE_HEX: Record<Plane, number> = { agent: GREY, guard: SIGNAL, money: WARN };
const PLANE_CSS: Record<Plane, string> = { agent: "#8a8a82", guard: "#00ff9c", money: "#ffb000" };

export function PipelineScene() {
  const mountRef = useRef<HTMLDivElement>(null);
  const labelLayerRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState<Stage | null>(null);
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const mount = mountRef.current;
    const labelLayer = labelLayerRef.current;
    if (!mount || !labelLayer) return;

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(BG, 0.03);

    const camera = new THREE.PerspectiveCamera(44, mount.clientWidth / mount.clientHeight, 0.1, 200);
    // A fixed three-quarter camera. This angle is what makes the scene read
    // as 3D, so it is never conditional on motion preference.
    camera.position.set(2.4, 3.4, 15.5);
    camera.lookAt(0, 0, -0.6);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.domElement.setAttribute("role", "img");
    renderer.domElement.setAttribute(
      "aria-label",
      "Three-dimensional diagram of the OrderGuard pipeline: intent, routing and connector selection on the near side of a trust boundary; authoritative re-read, twenty-two deterministic gates and signed authorization beyond it."
    );
    renderer.domElement.style.display = "block";
    mount.appendChild(renderer.domElement);

    const rig = new THREE.Group();
    // Offset the whole run down and right so it never sits under the copy
    // block in the upper left.
    rig.position.set(1.1, -1.5, 0);
    scene.add(rig);

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.2);
    key.position.set(6, 9, 10);
    scene.add(key);
    const rim = new THREE.PointLight(SIGNAL, 30, 26);
    rim.position.set(0, 0, -6);
    scene.add(rim);

    // --- trust boundary -----------------------------------------------
    const boundary = new THREE.Mesh(
      new THREE.PlaneGeometry(10, 8),
      new THREE.MeshBasicMaterial({ color: SIGNAL, transparent: true, opacity: 0.05, side: THREE.DoubleSide })
    );
    boundary.rotation.y = Math.PI / 2;
    boundary.position.set(0, 0, -0.4);
    rig.add(boundary);

    const boundaryEdge = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.PlaneGeometry(10, 8)),
      new THREE.LineDashedMaterial({ color: SIGNAL, transparent: true, opacity: 0.55, dashSize: 0.3, gapSize: 0.22 })
    );
    boundaryEdge.computeLineDistances();
    boundaryEdge.rotation.y = Math.PI / 2;
    boundaryEdge.position.set(0, 0, -0.4);
    rig.add(boundaryEdge);

    // --- stage slabs ---------------------------------------------------
    const slabs: { mesh: THREE.Mesh; stage: Stage; baseY: number }[] = [];
    STAGES.forEach((stage) => {
      const hex = PLANE_HEX[stage.plane];
      const geo = new THREE.BoxGeometry(3.0, 1.55, 0.18);
      const mat = new THREE.MeshStandardMaterial({
        color: 0x121212,
        emissive: hex,
        emissiveIntensity: 0.14,
        roughness: 0.5,
        metalness: 0.2,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(...stage.pos);
      mesh.userData.stageId = stage.id;

      // Hard wireframe edge — the brutalist border language, in 3D.
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: hex })
      );
      mesh.add(edges);

      rig.add(mesh);
      slabs.push({ mesh, stage, baseY: stage.pos[1] });
    });

    // --- conduits + packets ---------------------------------------------
    const packets: { mesh: THREE.Mesh; from: THREE.Vector3; to: THREE.Vector3; offset: number }[] = [];
    for (let i = 0; i < STAGES.length - 1; i++) {
      const a = new THREE.Vector3(...STAGES[i].pos);
      const b = new THREE.Vector3(...STAGES[i + 1].pos);
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([a, b]),
        new THREE.LineBasicMaterial({ color: i < 2 ? GREY : SIGNAL, transparent: true, opacity: 0.6 })
      );
      rig.add(line);

      const packet = new THREE.Mesh(
        new THREE.BoxGeometry(0.18, 0.18, 0.18),
        new THREE.MeshBasicMaterial({ color: SIGNAL, transparent: true, opacity: 0.95 })
      );
      packet.visible = false;
      rig.add(packet);
      packets.push({ mesh: packet, from: a, to: b, offset: i * 0.16 });
    }

    // --- ground grid ------------------------------------------------------
    const grid = new THREE.GridHelper(46, 46, 0x1f1f1f, 0x151515);
    grid.position.y = -5;
    rig.add(grid);

    // --- labels (real DOM, projected) --------------------------------------
    const labelEls = STAGES.map((stage, i) => {
      const el = document.createElement("div");
      el.className = "absolute pointer-events-none will-change-transform";
      el.style.transform = "translate(-50%,-50%)";
      el.innerHTML = `
        <div style="font-family:'JetBrains Mono',monospace;line-height:1.25;white-space:nowrap">
          <div style="font-size:9px;letter-spacing:.14em;color:#8a8a82;font-weight:700">${String(i + 1).padStart(2, "0")}</div>
          <div style="font-size:15px;letter-spacing:-0.01em;color:${PLANE_CSS[stage.plane]};font-weight:800">${stage.label}</div>
          <div style="font-size:9px;letter-spacing:.12em;color:#8a8a82;font-weight:700;margin-top:2px">${stage.sub}</div>
        </div>`;
      labelLayer.appendChild(el);
      return el;
    });

    // --- interaction --------------------------------------------------------
    const pointer = new THREE.Vector2(0, 0);
    const ndc = new THREE.Vector2();
    const raycaster = new THREE.Raycaster();
    let hoveredId: string | null = null;

    const onPointerMove = (e: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = (e.clientX - rect.left) / rect.width - 0.5;
      pointer.y = (e.clientY - rect.top) / rect.height - 0.5;
      ndc.x = pointer.x * 2;
      ndc.y = -pointer.y * 2;
    };
    const onPointerLeave = () => {
      pointer.set(0, 0);
      ndc.set(99, 99);
    };
    mount.addEventListener("pointermove", onPointerMove);
    mount.addEventListener("pointerleave", onPointerLeave);

    // --- resize -------------------------------------------------------------
    const resize = () => {
      if (!mount.clientWidth || !mount.clientHeight) return;
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(mount);

    // --- loop ---------------------------------------------------------------
    const clock = new THREE.Clock();
    const projected = new THREE.Vector3();
    let raf = 0;
    let beat = 0;
    let lastBeat = 0;

    const tick = () => {
      raf = requestAnimationFrame(tick);
      const t = clock.getElapsedTime();

      if (!reduced) {
        const drift = Math.sin(t * 0.15) * 0.06;
        rig.rotation.y += (drift + pointer.x * 0.18 - rig.rotation.y) * 0.045;
        rig.rotation.x += (-pointer.y * 0.1 - rig.rotation.x) * 0.045;

        if (t - lastBeat > 0.95) {
          lastBeat = t;
          beat = (beat + 1) % STAGES.length;
        }
      }

      // Hover test
      raycaster.setFromCamera(ndc, camera);
      const hits = raycaster.intersectObjects(slabs.map((s) => s.mesh), false);
      const nextId = hits.length ? (hits[0].object.userData.stageId as string) : null;
      if (nextId !== hoveredId) {
        hoveredId = nextId;
        setHovered(STAGES.find((s) => s.id === nextId) ?? null);
        renderer.domElement.style.cursor = nextId ? "pointer" : "default";
      }

      slabs.forEach((slab, i) => {
        if (!reduced) slab.mesh.position.y = slab.baseY + Math.sin(t * 0.62 + i) * 0.14;
        const isActive = hoveredId ? hoveredId === slab.stage.id : !reduced && beat === i;
        const mat = slab.mesh.material as THREE.MeshStandardMaterial;
        mat.emissiveIntensity += ((isActive ? 0.62 : 0.14) - mat.emissiveIntensity) * 0.12;
      });

      packets.forEach((p) => {
        if (reduced) return;
        p.mesh.visible = true;
        const local = (t * 0.5 + p.offset) % 1;
        p.mesh.position.lerpVectors(p.from, p.to, local);
        (p.mesh.material as THREE.MeshBasicMaterial).opacity = Math.sin(local * Math.PI);
      });

      renderer.render(scene, camera);

      // Project label positions after render so they track the slabs.
      slabs.forEach((slab, i) => {
        projected.copy(slab.mesh.position);
        slab.mesh.parent!.localToWorld(projected);
        projected.project(camera);
        const el = labelEls[i];
        const visible = projected.z < 1;
        el.style.opacity = visible ? "1" : "0";
        el.style.left = `${(projected.x * 0.5 + 0.5) * mount.clientWidth}px`;
        el.style.top = `${(-projected.y * 0.5 + 0.5) * mount.clientHeight}px`;
      });
    };
    tick();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      mount.removeEventListener("pointermove", onPointerMove);
      mount.removeEventListener("pointerleave", onPointerLeave);
      labelEls.forEach((el) => el.remove());
      renderer.dispose();
      scene.traverse((o) => {
        const any = o as THREE.Mesh;
        any.geometry?.dispose?.();
        const m = any.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(m)) m.forEach((x) => x.dispose());
        else m?.dispose?.();
      });
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [reduced]);

  return (
    <section
      className="relative border-2 border-border bg-background overflow-hidden"
      style={{ height: "min(74vh, 660px)" }}
      aria-label="OrderGuard pipeline, rendered in 3D"
    >
      <SpaceHorizon />
      <div ref={mountRef} className="absolute inset-0" />
      <div ref={labelLayerRef} className="absolute inset-0 pointer-events-none z-10" />

      <div className="absolute top-0 left-0 p-6 md:p-8 max-w-lg z-20 pointer-events-none">
        <Eyebrow className="mb-3">ORDERGUARD · Agentic Commerce Control Plane</Eyebrow>
        <StaggerHeading
          as="h1"
          text="FIND. VERIFY. PAY. PROVE."
          fontFamily="var(--font-sans)"
          className="text-[clamp(1.8rem,5vw,3.4rem)] font-extrabold tracking-tighter leading-[0.92] mb-4"
        />
        <motion.p
          className="text-[13px] leading-relaxed text-muted-foreground"
          initial={{ opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.3 }}
        >
          An agent may search, compare and propose.{" "}
          <span className="text-signal font-bold">Only deterministic code may authorize money.</span>
        </motion.p>
      </div>

      <div className="absolute bottom-0 left-0 right-0 p-4 md:p-6 flex flex-wrap items-end justify-between gap-3 pointer-events-none z-20">
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {[
            { k: "PROBABILISTIC", c: "bg-muted-foreground" },
            { k: "DETERMINISTIC", c: "bg-signal" },
            { k: "SETTLEMENT", c: "bg-warn" },
          ].map((l) => (
            <span key={l.k} className="label-micro text-muted-foreground flex items-center gap-1.5">
              <span className={`inline-block size-2 ${l.c}`} />
              {l.k}
            </span>
          ))}
        </div>
        <div className="label-micro text-muted-foreground border-2 border-border bg-card px-3 py-2 min-w-[230px]">
          {hovered ? (
            <>
              <span style={{ color: PLANE_CSS[hovered.plane] }}>{hovered.label}</span>
              <span className="block mt-1">{hovered.sub}</span>
            </>
          ) : (
            <span>{reduced ? "MOTION REDUCED — DEPTH PRESERVED" : "HOVER A STAGE TO INSPECT"}</span>
          )}
        </div>
      </div>
    </section>
  );
}
