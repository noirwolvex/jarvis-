"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MissionVoice } from "@/lib/mission-voice";
import type { Snapshot } from "@/lib/view-types";

export function useMissionVoice(snapshot: Snapshot, connected: boolean) {
  const [available, setAvailable] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<MissionVoice | null>(null);
  const latest = useRef(snapshot);
  latest.current = snapshot;

  useEffect(() => {
    if (!("speechSynthesis" in window) || typeof window.SpeechSynthesisUtterance !== "function") return;
    const speech = window.speechSynthesis;
    let disposed = false;
    let generation = 0;
    const voice = new MissionVoice({
      cancel() { generation++; speech.cancel(); },
      speak(text) {
        const current = generation;
        const failed = () => {
          if (disposed || current !== generation) return;
          voice.disable();
          setEnabled(false);
          setError("Speech is unavailable. Status updates remain in the execution timeline.");
        };
        try {
          const utterance = new SpeechSynthesisUtterance(text);
          utterance.lang = "en-US";
          utterance.rate = 1.08;
          utterance.onerror = event => {
            if (event.error !== "interrupted" && event.error !== "canceled") failed();
          };
          speech.speak(utterance);
        } catch { failed(); }
      },
    });
    controller.current = voice;
    setAvailable(true);
    return () => { disposed = true; voice.disable(); controller.current = null; };
  }, []);

  useEffect(() => {
    if (!connected) {
      controller.current?.interrupt();
      controller.current?.baseline(snapshot.events);
      return;
    }
    controller.current?.update(snapshot.events, snapshot.emergencyStopped);
  }, [snapshot, connected]);

  const toggle = useCallback(() => {
    const voice = controller.current;
    if (!voice) return;
    setError("");
    if (enabled) { voice.disable(); setEnabled(false); }
    else { setEnabled(true); voice.enable(latest.current.events); }
  }, [enabled]);

  const interrupt = useCallback(() => controller.current?.interrupt(), []);
  return { available, enabled, error, toggle, interrupt };
}
