import React, { useEffect, useRef } from "react";
import { CheckCircle2, Mic, MicOff, Phone, PhoneOff, X, XCircle } from "lucide-react";

function formatTimer(sec) {
  const m = String(Math.floor(sec / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

export default function PhoneModal({
  open,
  callState,
  isMuted,
  elapsedSeconds,
  micLevel = 0,
  error,
  analyserRef,
  transcript,
  interimText = "",
  activeTool,
  confirmationCard,
  onToggleMute,
  onEndCall,
  onStartCall,
  onClose,
}) {
  const canvasRef = useRef(null);
  const scrollRef = useRef(null);

  // Auto-scroll live transcript inside the handset
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transcript, interimText]);

  // Live reactive waveform driven by Web Audio AnalyserNode
  useEffect(() => {
    if (!open) return;
    let rafId;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    const bars = 30;
    const draw = (time) => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      let freqData = null;
      if (analyserRef.current && callState === "connected") {
        freqData = new Uint8Array(analyserRef.current.frequencyBinCount);
        analyserRef.current.getByteFrequencyData(freqData);
      }

      const gap = w / bars;
      const barW = Math.max(2.5, gap * 0.42);

      for (let i = 0; i < bars; i++) {
        let level = 0.08;
        if (callState === "calling") {
          level = 0.14 + 0.16 * Math.abs(Math.sin(time * 0.004 + i * 0.35));
        } else if (callState === "connected" && freqData) {
          const idx = Math.floor((i / bars) * (freqData.length * 0.65));
          const raw = freqData[idx] / 255;
          const centerWeight = 1 - (Math.abs(i - bars / 2) / (bars / 2)) * 0.45;
          level = Math.max(0.08, raw * centerWeight);
        }

        const barH = Math.max(4, level * (h - 8));
        const x = i * gap + (gap - barW) / 2;
        const y = (h - barH) / 2;

        ctx.fillStyle =
          callState === "connected"
            ? "rgba(78, 212, 158, 0.85)"
            : callState === "calling"
            ? "rgba(226, 164, 71, 0.72)"
            : "rgba(255, 255, 255, 0.18)";

        ctx.beginPath();
        ctx.roundRect(x, y, barW, barH, 3);
        ctx.fill();
      }
      rafId = requestAnimationFrame(draw);
    };

    rafId = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafId);
  }, [open, callState, analyserRef]);

  if (!open) return null;

  const statusLabel =
    callState === "calling"
      ? "Ringing…"
      : callState === "connected"
      ? formatTimer(elapsedSeconds)
      : "Call Ended";

  return (
    <div className="phone-backdrop" onClick={callState === "ended" ? onClose : undefined}>
      <div className="handset" onClick={(e) => e.stopPropagation()}>
        <div className="handset-notch">
          <span className={`notch-dot ${callState === "connected" ? "active" : ""}`} />
        </div>

        <div className="handset-header">
          <button
            className="handset-close"
            onClick={onClose}
            title="Minimize"
            aria-label="Close phone overlay"
          >
            <X size={15} />
          </button>
          <div className="handset-name serif">Meridian Reception</div>
          <div className="handset-state mono">
            <span className={`state-pill-dot ${callState}`} />
            <span>{statusLabel}</span>
          </div>
          {error && (
            <div style={{ color: "#f08470", fontSize: 12, marginTop: 8 }}>{error}</div>
          )}
        </div>

        <div className="waveform-wrap">
          <canvas ref={canvasRef} className="waveform-canvas" />
          {callState === "connected" && (
            <div
              style={{
                fontSize: 11,
                color: micLevel > 0.06 ? "#5fd6a5" : "rgba(243,241,235,0.45)",
              }}
              className="mono"
            >
              {isMuted ? "Mic muted" : micLevel > 0.06 ? "● Voice detected" : "○ Listening…"}
            </div>
          )}
          {activeTool && callState === "connected" && (
            <div className="tool-toast mono">{activeTool}</div>
          )}
        </div>

        {confirmationCard && (
          <div
            className={`confirm-card ${
              confirmationCard.kind === "cancelled" ? "cancelled" : ""
            }`}
          >
            <div className="confirm-top mono">
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                {confirmationCard.kind === "cancelled" ? (
                  <XCircle size={13} />
                ) : (
                  <CheckCircle2 size={13} />
                )}
                {confirmationCard.kind === "cancelled" ? "Cancelled" : "Confirmed"}
              </span>
              <span>{confirmationCard.id}</span>
            </div>
            <div className="confirm-doc">{confirmationCard.doctor_name}</div>
            <div className="confirm-meta mono">
              {confirmationCard.slot_date} · {confirmationCard.slot_time} ·{" "}
              {confirmationCard.caller_phone_display}
            </div>
          </div>
        )}

        <div className="phone-transcript" ref={scrollRef}>
          {transcript.length === 0 && !interimText ? (
            <div className="phone-placeholder">
              {callState === "calling"
                ? "Connecting audio line…"
                : "Speak naturally. State your phone number when booking or cancelling."}
            </div>
          ) : (
            <>
              {transcript.map((item, idx) => (
                <div key={idx} className={`bubble ${item.role}`}>
                  {item.text}
                </div>
              ))}
              {interimText && (
                <div
                  className="bubble caller"
                  style={{ opacity: 0.65, fontStyle: "italic" }}
                >
                  {interimText}…
                </div>
              )}
            </>
          )}
        </div>

        <div className="handset-controls">
          {callState !== "ended" ? (
            <>
              <div className="ctrl-col">
                <button
                  className={`round-btn mute ${isMuted ? "muted" : ""}`}
                  onClick={onToggleMute}
                  disabled={callState !== "connected"}
                  aria-label={isMuted ? "Unmute" : "Mute"}
                >
                  {isMuted ? <MicOff size={21} /> : <Mic size={21} />}
                </button>
                <span className="ctrl-label">{isMuted ? "Unmute" : "Mute"}</span>
              </div>

              <div className="ctrl-col">
                <button
                  className="round-btn end"
                  onClick={onEndCall}
                  aria-label="End call"
                >
                  <PhoneOff size={23} />
                </button>
                <span className="ctrl-label">End</span>
              </div>
            </>
          ) : (
            <div className="ctrl-col">
              <button
                className="round-btn recall"
                onClick={onStartCall}
                aria-label="Call again"
              >
                <Phone size={22} />
              </button>
              <span className="ctrl-label">Call Again</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}