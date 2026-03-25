import { useEffect, useRef, useCallback } from "react";

interface UsePollingOptions {
  intervalMs: number;
  enabled: boolean;
  onTick: () => void | Promise<void>;
  immediate?: boolean; // fire immediately on mount
}

export function usePolling({ intervalMs, enabled, onTick, immediate = true }: UsePollingOptions) {
  const tickRef = useRef(onTick);
  tickRef.current = onTick;

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const start = useCallback(() => {
    stop();
    if (immediate) {
      void tickRef.current();
    }
    intervalRef.current = setInterval(() => {
      void tickRef.current();
    }, intervalMs);
  }, [intervalMs, immediate, stop]);

  useEffect(() => {
    if (enabled) {
      start();
    } else {
      stop();
    }
    return stop;
  }, [enabled, start, stop]);
}
