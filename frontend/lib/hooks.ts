"use client";
import { useEffect, useRef, useState } from "react";

/** Call `fn` now and every `ms` while `active`; stops when the component unmounts. */
export function usePoll(fn: () => Promise<unknown> | void, ms: number, active = true) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        await ref.current();
      } finally {
        if (alive) timer = setTimeout(tick, ms);
      }
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [ms, active]);
}

export function useReducedMotion(): boolean {
  const [r, setR] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    setR(m.matches);
    const on = () => setR(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return r;
}

/** Animate a number towards `target` (eases out); returns the current value. */
export function useCountUp(target: number, ms = 900): number {
  const [v, setV] = useState(0);
  const reduced = useReducedMotion();
  const from = useRef(0);
  useEffect(() => {
    if (reduced) {
      setV(target);
      return;
    }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const step = (now: number) => {
      const k = Math.min(1, (now - start) / ms);
      const e = 1 - Math.pow(1 - k, 3);
      const cur = a + (target - a) * e;
      setV(cur);
      if (k < 1) raf = requestAnimationFrame(step);
      else from.current = target;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms, reduced]);
  return v;
}

/** True once the element has been on screen (used to start a one-off animation when it is seen). */
export function useSeen<T extends Element>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && setSeen(true), { threshold: 0.25 });
    io.observe(el);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen];
}

/** Tiny global toast: call toast("Saved"); <Toaster/> renders it. */
type Listener = (m: string | null) => void;
const listeners = new Set<Listener>();
export function toast(message: string) {
  listeners.forEach((l) => l(message));
  setTimeout(() => listeners.forEach((l) => l(null)), 2800);
}
export function useToast(): string | null {
  const [m, setM] = useState<string | null>(null);
  useEffect(() => {
    listeners.add(setM);
    return () => {
      listeners.delete(setM);
    };
  }, []);
  return m;
}
