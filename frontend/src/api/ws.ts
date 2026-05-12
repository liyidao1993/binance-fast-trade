import { useEffect } from "react";
import { useMarketStore } from "@/stores/market";
import { useOrdersStore } from "@/stores/orders";

type WsMsg = { topic: string; event: string; data: any };

type Handler = (msg: WsMsg) => void;

class WsClient {
  private ws: WebSocket | null = null;
  private url: string;
  private topics = new Set<string>();
  private handlers = new Set<Handler>();
  private reconnectDelayMs = 1000;
  private maxDelayMs = 15000;
  private stopped = false;
  private pingTimer: number | null = null;

  constructor() {
    // 开发下走 vite 代理；生产走同源
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    this.url = `${scheme}//${location.host}/api/v1/ws`;
  }

  connect() {
    if (this.stopped) return;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return;

    try {
      this.ws = new WebSocket(this.url);
    } catch (e) {
      console.error("ws connect failed", e);
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectDelayMs = 1000;
      // 重连后恢复订阅
      for (const t of this.topics) {
        this.send({ op: "subscribe", topic: t });
      }
      // 心跳
      if (this.pingTimer) window.clearInterval(this.pingTimer);
      this.pingTimer = window.setInterval(() => this.send({ op: "ping" }), 20000);
    };

    this.ws.onmessage = (ev) => {
      let msg: WsMsg | null = null;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (!msg) return;
      for (const h of this.handlers) {
        try {
          h(msg);
        } catch (e) {
          console.error("ws handler error", e);
        }
      }
    };

    this.ws.onclose = () => {
      if (this.pingTimer) {
        window.clearInterval(this.pingTimer);
        this.pingTimer = null;
      }
      this.ws = null;
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      // onclose 也会触发，这里只记录
    };
  }

  private scheduleReconnect() {
    if (this.stopped) return;
    const delay = this.reconnectDelayMs;
    this.reconnectDelayMs = Math.min(delay * 2, this.maxDelayMs);
    window.setTimeout(() => this.connect(), delay);
  }

  disconnect() {
    this.stopped = true;
    if (this.pingTimer) {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close(1000, "client disconnect");
      } catch {}
      this.ws = null;
    }
  }

  reopen() {
    this.stopped = false;
    this.connect();
  }

  subscribe(topic: string) {
    this.topics.add(topic);
    this.send({ op: "subscribe", topic });
  }

  unsubscribe(topic: string) {
    this.topics.delete(topic);
    this.send({ op: "unsubscribe", topic });
  }

  on(h: Handler): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  private send(obj: unknown) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify(obj));
    } catch {
      // ignore
    }
  }
}

export const ws = new WsClient();

/** React hook：根据登录态自动连/断；每次渲染都更新 store 绑定 */
export function useWs(authed: boolean) {
  const updateTicker = useMarketStore((s) => s.updateTicker);
  const onOrderEvent = useOrdersStore((s) => s.onOrderEvent);

  useEffect(() => {
    if (!authed) {
      ws.disconnect();
      return;
    }
    ws.reopen();
    const off = ws.on((msg) => {
      if (msg.topic.startsWith("ticker:") && msg.event === "update") {
        const sym = msg.topic.slice("ticker:".length);
        updateTicker(sym, msg.data);
      } else if (msg.topic === "tickers" && msg.event === "snapshot") {
        const snap: Record<string, any> = msg.data || {};
        for (const [sym, tk] of Object.entries(snap)) {
          updateTicker(sym, tk);
        }
      } else if (msg.topic === "orders" && msg.event === "update") {
        onOrderEvent(msg.data);
      }
    });
    return () => {
      off();
    };
  }, [authed, updateTicker, onOrderEvent]);
}
