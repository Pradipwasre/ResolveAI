"use client";

import { useEffect, useRef, useState } from "react";
import { wsUrl } from "./api";

export type LiveEvent = { event: string; ticket_id?: number; [key: string]: unknown };

/** Subscribes to live events over WebSocket and reconnects if the connection drops. */
export function useLiveEvents(onEvent: (event: LiveEvent) => void): boolean {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout>;
    let ping: ReturnType<typeof setInterval>;
    let closed = false;

    const connect = () => {
      socket = new WebSocket(wsUrl());
      socket.onopen = () => {
        setConnected(true);
        ping = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send("ping"), 25000);
      };
      socket.onmessage = (message) => {
        try {
          handler.current(JSON.parse(message.data));
        } catch {}
      };
      socket.onclose = () => {
        setConnected(false);
        clearInterval(ping);
        if (!closed) retry = setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      clearInterval(ping);
      socket?.close();
    };
  }, []);

  return connected;
}

/** Runs `fn` now and every `ms` milliseconds (a fallback when live events are unavailable). */
export function useInterval(fn: () => void, ms: number) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    const id = setInterval(() => saved.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}
