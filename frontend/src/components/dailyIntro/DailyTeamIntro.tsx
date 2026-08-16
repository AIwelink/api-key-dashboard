import { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import logoUrl from "../../../AIwelink_logo_bule_A.png";
import type { User } from "../../types";
import {
  claimDailyIntro,
  shouldShowDailyIntro,
  type DailyIntroMember,
  type DailyIntroStorage,
} from "./dailyIntro";
import "./DailyTeamIntro.css";

export const DAILY_TEAM_MESSAGE = "世事浮沉，皆为淬炼；商海浩瀚，唯有坚毅。愿君心有赤焰，足履薄冰，终见日月新天。";

type DailyTeamIntroStage = "opening" | "reduced" | "exiting";

const STANDARD_HOLD_MS = 3_000;
const STANDARD_EXIT_MS = 900;
const REDUCED_HOLD_MS = 300;
const REDUCED_EXIT_MS = 300;
const SKIP_EXIT_MS = 360;
const SIGNED_OUT_VISITOR: DailyIntroMember = {
  id: "signed-out-visitor",
  email: "signed-out-visitor@local",
};

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export function DailyTeamIntro({ onComplete }: { onComplete: () => void }) {
  const reducedMotion = useRef(prefersReducedMotion()).current;
  const [stage, setStage] = useState<DailyTeamIntroStage>(reducedMotion ? "reduced" : "opening");
  const completionScheduled = useRef(false);
  const timers = useRef<number[]>([]);

  const clearTimers = useCallback(() => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
  }, []);

  const beginExit = useCallback((duration: number) => {
    if (completionScheduled.current) return;
    completionScheduled.current = true;
    clearTimers();
    setStage("exiting");
    timers.current = [window.setTimeout(onComplete, duration)];
  }, [clearTimers, onComplete]);

  useEffect(() => {
    const root = document.documentElement;
    const previousOverflow = root.style.overflow;
    root.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") beginExit(SKIP_EXIT_MS);
    };
    window.addEventListener("keydown", handleKeyDown);

    timers.current = [window.setTimeout(
      () => beginExit(reducedMotion ? REDUCED_EXIT_MS : STANDARD_EXIT_MS),
      reducedMotion ? REDUCED_HOLD_MS : STANDARD_HOLD_MS,
    )];

    return () => {
      clearTimers();
      window.removeEventListener("keydown", handleKeyDown);
      root.style.overflow = previousOverflow;
    };
  }, [beginExit, clearTimers, reducedMotion]);

  return (
    <section
      aria-label="AIwelink 每日团队寄语"
      aria-modal="true"
      className="daily-team-intro"
      data-stage={stage}
      role="dialog"
    >
      <button
        aria-label="跳过开场"
        className="daily-team-intro-skip"
        onClick={() => beginExit(SKIP_EXIT_MS)}
        title="跳过开场"
        type="button"
      >
        <X aria-hidden="true" size={18} strokeWidth={1.8} />
      </button>

      <div className="daily-team-intro-content">
        <img alt="AIwelink" className="daily-team-intro-logo" src={logoUrl} />
        <p aria-label={DAILY_TEAM_MESSAGE} className="daily-team-intro-message">
          <span>世事浮沉，皆为淬炼；商海浩瀚，唯有坚毅。</span>
          <span>愿君心有赤焰，足履薄冰，终见日月新天。</span>
        </p>
      </div>
    </section>
  );
}

type DailyTeamIntroGateProps = {
  user: Pick<User, "id" | "email"> | null;
  storage?: DailyIntroStorage;
  now?: Date;
};

export function DailyTeamIntroGate({ user, storage, now }: DailyTeamIntroGateProps) {
  const resolvedStorage = storage ?? window.localStorage;
  const [claimedAt] = useState(() => now ?? new Date());
  const [audience] = useState<DailyIntroMember>(() => user ?? SIGNED_OUT_VISITOR);
  const [visible, setVisible] = useState(() => (
    shouldShowDailyIntro(resolvedStorage, audience, claimedAt)
  ));

  useEffect(() => {
    if (visible) claimDailyIntro(resolvedStorage, audience, claimedAt);
  }, [audience, claimedAt, resolvedStorage, visible]);

  useEffect(() => {
    if (audience === SIGNED_OUT_VISITOR && user) {
      claimDailyIntro(resolvedStorage, user, claimedAt);
    }
  }, [audience, claimedAt, resolvedStorage, user?.email, user?.id]);

  const handleComplete = useCallback(() => setVisible(false), []);

  return visible ? <DailyTeamIntro onComplete={handleComplete} /> : null;
}
