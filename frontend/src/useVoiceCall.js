import { useCallback, useEffect, useRef, useState } from "react";
import { callSounds } from "./sounds";

export function useVoiceCall() {
  const [callState, setCallState] = useState("idle"); // idle | calling | connected | ended
  const [isMuted, setIsMuted] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [micLevel, setMicLevel] = useState(0);
  const [error, setError] = useState(null);

  const pcRef = useRef(null);
  const localStreamRef = useRef(null);
  const remoteAudioRef = useRef(null);
  const pcIdRef = useRef(null);
  const audioCtxRef = useRef(null);
  const analyserRef = useRef(null);
  const micAnalyserRef = useRef(null);
  const timerRef = useRef(null);
  const meterRafRef = useRef(null);

  const cleanupMedia = useCallback(() => {
    callSounds.stopRingback();
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (meterRafRef.current) {
      cancelAnimationFrame(meterRafRef.current);
      meterRafRef.current = null;
    }
    if (pcRef.current) {
      pcRef.current.onicecandidate = null;
      pcRef.current.ontrack = null;
      pcRef.current.onconnectionstatechange = null;
      pcRef.current.close();
      pcRef.current = null;
    }
    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach((t) => t.stop());
      localStreamRef.current = null;
    }
    if (remoteAudioRef.current) {
      remoteAudioRef.current.pause();
      remoteAudioRef.current.srcObject = null;
      remoteAudioRef.current.remove();
      remoteAudioRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
      analyserRef.current = null;
      micAnalyserRef.current = null;
    }
    pcIdRef.current = null;
    setMicLevel(0);
  }, []);

  const attachToAnalyser = useCallback((stream, isMic = false) => {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!audioCtxRef.current) {
        audioCtxRef.current = new AudioCtx();
      }
      if (audioCtxRef.current.state === "suspended") {
        audioCtxRef.current.resume();
      }
      if (!analyserRef.current) {
        const analyser = audioCtxRef.current.createAnalyser();
        analyser.fftSize = 128;
        analyser.smoothingTimeConstant = 0.78;
        analyserRef.current = analyser;
      }
      const source = audioCtxRef.current.createMediaStreamSource(stream);
      source.connect(analyserRef.current);

      if (isMic) {
        const micAnalyser = audioCtxRef.current.createAnalyser();
        micAnalyser.fftSize = 128;
        source.connect(micAnalyser);
        micAnalyserRef.current = micAnalyser;

        const data = new Uint8Array(micAnalyser.frequencyBinCount);
        const tick = () => {
          if (!micAnalyserRef.current) return;
          micAnalyserRef.current.getByteTimeDomainData(data);
          let sum = 0;
          for (let i = 0; i < data.length; i++) {
            const v = (data[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.min(1, Math.sqrt(sum / data.length) * 4.5);
          setMicLevel(rms);
          meterRafRef.current = requestAnimationFrame(tick);
        };
        meterRafRef.current = requestAnimationFrame(tick);
      }
    } catch {
      // Graceful fallback
    }
  }, []);

  const startCall = useCallback(async () => {
    setError(null);
    setIsMuted(false);
    setElapsedSeconds(0);
    setCallState("calling");
    callSounds.startRingback();

    // 1. Pre-create and unlock the <audio> element synchronously inside the click gesture
    // so Chrome/Linux never blocks remote WebRTC audio playback.
    const audioEl = document.createElement("audio");
    audioEl.autoplay = true;
    audioEl.playsInline = true;
    audioEl.volume = 1.0;
    document.body.appendChild(audioEl);
    remoteAudioRef.current = audioEl;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      localStreamRef.current = stream;
      attachToAnalyser(stream, true);

      const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      });
      pcRef.current = pc;

      pc.createDataChannel("chat", { ordered: true });
      stream.getAudioTracks().forEach((track) => pc.addTrack(track, stream));

      const pendingCandidates = [];
      pc.onicecandidate = async (event) => {
        if (!event.candidate) return;
        const cand = {
          candidate: event.candidate.candidate,
          sdp_mid: event.candidate.sdpMid,
          sdp_mline_index: event.candidate.sdpMLineIndex,
        };
        if (pcIdRef.current) {
          fetch("/api/offer", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pc_id: pcIdRef.current, candidates: [cand] }),
          }).catch(() => {});
        } else {
          pendingCandidates.push(cand);
        }
      };

      pc.ontrack = async (event) => {
        if (event.track.kind !== "audio") return;
        const remoteStream =
          event.streams && event.streams[0]
            ? event.streams[0]
            : new MediaStream([event.track]);

        if (remoteAudioRef.current) {
          remoteAudioRef.current.srcObject = remoteStream;
          try {
            await remoteAudioRef.current.play();
          } catch (playErr) {
            setError("Audio blocked by browser — click anywhere on the phone screen.");
          }
        }
        attachToAnalyser(remoteStream, false);
      };

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
          setCallState("connected");
          callSounds.playConnected();
          if (!timerRef.current) {
            timerRef.current = setInterval(() => {
              setElapsedSeconds((s) => s + 1);
            }, 1000);
          }
        } else if (["failed", "closed", "disconnected"].includes(pc.connectionState)) {
          setCallState((prev) => (prev === "idle" ? "idle" : "ended"));
          cleanupMedia();
        }
      };

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      await new Promise((resolve) => {
        if (pc.iceGatheringState === "complete") return resolve();
        const timeout = setTimeout(resolve, 750);
        pc.onicegatheringstatechange = () => {
          if (pc.iceGatheringState === "complete") {
            clearTimeout(timeout);
            resolve();
          }
        };
      });

      const res = await fetch("/api/offer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sdp: pc.localDescription.sdp,
          type: pc.localDescription.type,
        }),
      });

      if (!res.ok) {
        throw new Error(`Signaling failed (${res.status})`);
      }

      const answer = await res.json();
      pcIdRef.current = answer.pc_id;

      await pc.setRemoteDescription(
        new RTCSessionDescription({ sdp: answer.sdp, type: answer.type })
      );

      if (pendingCandidates.length > 0 && pcIdRef.current) {
        fetch("/api/offer", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pc_id: pcIdRef.current,
            candidates: pendingCandidates,
          }),
        }).catch(() => {});
      }
    } catch (err) {
      cleanupMedia();
      setError(err.message || "Microphone or server connection failed");
      setCallState("ended");
    }
  }, [attachToAnalyser, cleanupMedia]);

  const endCall = useCallback(() => {
    callSounds.playEnded();
    cleanupMedia();
    setCallState("ended");
  }, [cleanupMedia]);

  const toggleMute = useCallback(() => {
    if (!localStreamRef.current) return;
    localStreamRef.current.getAudioTracks().forEach((track) => {
      track.enabled = isMuted;
    });
    setIsMuted((m) => !m);
  }, [isMuted]);

  const resetCallState = useCallback(() => {
    cleanupMedia();
    setCallState("idle");
    setElapsedSeconds(0);
    setError(null);
  }, [cleanupMedia]);

  useEffect(() => () => cleanupMedia(), [cleanupMedia]);

  return {
    callState,
    isMuted,
    elapsedSeconds,
    micLevel,
    error,
    analyserRef,
    startCall,
    endCall,
    toggleMute,
    resetCallState,
  };
}