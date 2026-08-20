/**
 * The console's link to the Python twin.
 *
 * The backend is the ONLY model. The old console carried a second simulation
 * engine in the browser and used it for the What-if preview and every scrubbed
 * frame, so the trajectory drawn on the map came from a different model than
 * the numbers printed beside it, and the two could disagree. When the backend
 * is unreachable the console now says so plainly rather than quietly showing a
 * shadow model's opinion.
 */
import type { ConnectionStatus, TwinBundle } from "@/domain/types";

const RECONNECT_MS = 2000;

export class TwinSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private timer: number | null = null;

  constructor(
    private readonly url: string,
    private readonly onBundle: (b: TwinBundle) => void,
    private readonly onStatus: (s: ConnectionStatus) => void,
  ) {}

  connect(): void {
    if (this.closed) return;
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => this.onStatus("CONNECTED");
    this.ws.onmessage = (ev) => {
      let msg: TwinBundle;
      try {
        msg = JSON.parse(ev.data as string) as TwinBundle;
      } catch {
        return;
      }
      if (!isBundle(msg)) return;
      this.onBundle(msg);
    };
    this.ws.onclose = () => {
      if (this.closed) return;
      this.onStatus("RECONNECTING");
      this.scheduleReconnect();
    };
    this.ws.onerror = () => this.ws?.close();
  }

  private scheduleReconnect(): void {
    if (this.closed || this.timer !== null) return;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      this.connect();
    }, RECONNECT_MS);
  }

  send(msg: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  close(): void {
    this.closed = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }
}

function isBundle(value: unknown): value is TwinBundle {
  if (!value || typeof value !== "object") return false;
  const frame = value as Partial<TwinBundle>;
  return (
    !!frame.simState &&
    typeof frame.simState === "object" &&
    !!frame.prediction &&
    Array.isArray(frame.prediction.conflicts) &&
    !!frame.kpis
  );
}
