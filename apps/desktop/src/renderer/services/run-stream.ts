import { api, getBackendOrigin } from "./api";
import { isRunTerminalStatus } from "./contract";
import type { ApiRunSnapshot } from "../types";

interface RunSubscriptionOptions {
  runId: string;
  onUpdate: (snapshot: ApiRunSnapshot) => void;
  onModeChange?: (mode: "sse" | "polling") => void;
  onError?: (error: Error) => void;
  pollIntervalMs?: number;
}

export const createRunSubscription = async ({
  runId,
  onUpdate,
  onModeChange,
  onError,
  pollIntervalMs = 3000
}: RunSubscriptionOptions) => {
  let stopped = false;
  let eventSource: EventSource | null = null;
  let pollTimer: number | null = null;

  const stop = () => {
    stopped = true;
    eventSource?.close();
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const poll = async () => {
    if (stopped) {
      return;
    }

    try {
      const snapshot = await api.getRun(runId);
      onUpdate(snapshot);
      if (isRunTerminalStatus(snapshot.status)) {
        stop();
      }
    } catch (error) {
      onError?.(error as Error);
    }
  };

  const startPolling = () => {
    onModeChange?.("polling");
    void poll();
    pollTimer = window.setInterval(() => {
      void poll();
    }, pollIntervalMs);
  };

  try {
    const origin = await getBackendOrigin();
    const eventUrl = new URL(`/api/runs/${runId}/events`, origin).toString();
    eventSource = new EventSource(eventUrl);
    onModeChange?.("sse");

    eventSource.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as ApiRunSnapshot;
        onUpdate(payload);
        if (isRunTerminalStatus(payload.status)) {
          stop();
        }
      } catch (error) {
        onError?.(error as Error);
      }
    };

    eventSource.onerror = () => {
      eventSource?.close();
      if (stopped) {
        return;
      }
      startPolling();
    };
  } catch (error) {
    onError?.(error as Error);
    startPolling();
  }

  return stop;
};
