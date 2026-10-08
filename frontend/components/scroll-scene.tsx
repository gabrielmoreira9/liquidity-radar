"use client";
import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";

/** Keep scroll progress local to each scene. CSS provides the static reduced-motion path. */
function useElementScrollProgress(
  target: React.RefObject<HTMLElement | null>,
  mode: "scene" | "hero",
) {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const element = target.current;
    if (!element) return;

    const update = () => {
      const rect = element.getBoundingClientRect();
      const top = rect.top + window.scrollY;
      const start = mode === "hero" ? top : top - window.innerHeight;
      const end = mode === "hero" ? top + rect.height : top + rect.height;
      setProgress(
        Math.min(1, Math.max(0, (window.scrollY - start) / (end - start))),
      );
    };

    update();
    window.addEventListener("scroll", update, { passive: true });
    document.addEventListener("scroll", update, { passive: true, capture: true });
    document.documentElement.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      document.removeEventListener("scroll", update, true);
      document.documentElement.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [mode, target]);

  return progress;
}

export function ScrollScene({
  children,
  id,
  tone = "cyan",
  expand = false,
}: {
  children: React.ReactNode;
  id: string;
  tone?: "cyan" | "blue" | "violet";
  expand?: boolean;
}) {
  const target = useRef<HTMLElement>(null);
  const composition = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  const scrollYProgress = useElementScrollProgress(target, "scene");
  useEffect(() => {
    if (reduced) return;
    const update = () => {
      const element = target.current;
      const content = composition.current;
      if (!element || !content) return;
      const rect = element.getBoundingClientRect();
      const start = rect.top + window.scrollY - window.innerHeight;
      const progress = Math.min(
        1,
        Math.max(0, (window.scrollY - start) / (rect.height + window.innerHeight)),
      );
      const scale = (expand ? 0.76 : 0.9) + progress * (expand ? 0.3 : 0.1);
      const y = 120 - progress * 220;
      content.style.transform = `translate3d(0, ${y}px, 0) scale(${scale})`;
      content.style.opacity = String(Math.min(1, 0.2 + progress * 1.2));
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    const interval = window.setInterval(update, 50);
    return () => {
      window.removeEventListener("scroll", update);
      window.clearInterval(interval);
    };
  }, [expand, reduced]);
  const scale =
    scrollYProgress < 0.2
      ? (expand ? 0.76 : 0.9) + scrollYProgress * (expand ? 0.55 : 0.4)
      : scrollYProgress < 0.52
        ? (expand ? 0.87 : 0.98) +
          ((scrollYProgress - 0.2) / 0.32) * (expand ? 0.19 : 0.04)
        : scrollYProgress < 0.82
          ? (expand ? 1.06 : 1.02) -
            ((scrollYProgress - 0.52) / 0.3) * (expand ? 0.06 : 0.02)
          : 1 - ((scrollYProgress - 0.82) / 0.18) * 0.05;
  const y =
    scrollYProgress < 0.2
      ? 120 - scrollYProgress * 600
      : scrollYProgress < 0.65
        ? 0
        : (scrollYProgress - 0.65) * -285.7;
  const opacity =
    scrollYProgress < 0.16
      ? 0.05 + (scrollYProgress / 0.16) * 0.7
      : scrollYProgress < 0.42
        ? 0.75 + ((scrollYProgress - 0.16) / 0.26) * 0.25
        : scrollYProgress < 0.78
          ? 1
          : 1 - ((scrollYProgress - 0.78) / 0.22) * 0.82;
  const lightOpacity =
    scrollYProgress < 0.28
      ? (scrollYProgress / 0.28) * 0.9
      : scrollYProgress < 0.55
        ? 0.9 - ((scrollYProgress - 0.28) / 0.27) * 0.32
        : Math.max(0, 0.58 - ((scrollYProgress - 0.55) / 0.45) * 0.58);
  return (
    <section
      ref={target}
      id={id}
      className={`scroll-scene scene-${tone}${expand ? " scene-expand" : ""}`}
      data-scroll-scene
    >
      <div className="scene-pin">
        <motion.div
          aria-hidden="true"
          className="scene-light"
          style={{ opacity: reduced ? 0.4 : lightOpacity }}
        />
        <motion.div
          ref={composition}
          className="scene-content"
          data-scroll-composition
          style={reduced ? undefined : { scale, y, opacity }}
        >
          {children}
        </motion.div>
      </div>
    </section>
  );
}
export function HeroStage({ children }: { children: React.ReactNode }) {
  const target = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const scrollYProgress = useElementScrollProgress(target, "hero");
  const scale = 1 + scrollYProgress * 0.2;
  const y = scrollYProgress * 150;
  const opacity =
    scrollYProgress < 0.58
      ? 1 - scrollYProgress * 0.35 / 0.58
      : 0.65 - ((scrollYProgress - 0.58) / 0.42) * 0.65;
  return (
    <section ref={target} className="launch-hero" data-hero-stage>
      <motion.div
        className="hero-composition"
        style={reduced ? undefined : { scale, y, opacity }}
      >
        {children}
      </motion.div>
    </section>
  );
}
