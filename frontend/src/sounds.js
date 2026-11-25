class CallSounds {
  constructor() {
    this.ctx = null;
    this.ringInterval = null;
  }

  _ensureCtx() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AudioCtx();
    }
    if (this.ctx.state === "suspended") {
      this.ctx.resume();
    }
    return this.ctx;
  }

  startRingback() {
    this.stopRingback();
    const playBurst = () => {
      try {
        const ctx = this._ensureCtx();
        const now = ctx.currentTime;
        const gain = ctx.createGain();
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.035, now + 0.04);
        gain.gain.setValueAtTime(0.035, now + 0.9);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 1.0);
        gain.connect(ctx.destination);

        // Dual-tone US/standard ringback: 440Hz + 480Hz
        [440, 480].forEach((freq) => {
          const osc = ctx.createOscillator();
          osc.type = "sine";
          osc.frequency.setValueAtTime(freq, now);
          osc.connect(gain);
          osc.start(now);
          osc.stop(now + 1.05);
        });
      } catch {
        // Ignore autoplay restrictions
      }
    };
    playBurst();
    this.ringInterval = setInterval(playBurst, 2400);
  }

  stopRingback() {
    if (this.ringInterval) {
      clearInterval(this.ringInterval);
      this.ringInterval = null;
    }
  }

  playConnected() {
    this.stopRingback();
    try {
      const ctx = this._ensureCtx();
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(523.25, now);
      osc.frequency.exponentialRampToValueAtTime(659.25, now + 0.11);
      gain.gain.setValueAtTime(0.001, now);
      gain.gain.exponentialRampToValueAtTime(0.045, now + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.2);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.22);
    } catch {
      // Ignore
    }
  }

  playEnded() {
    this.stopRingback();
    try {
      const ctx = this._ensureCtx();
      const now = ctx.currentTime;
      [420, 320].forEach((freq, idx) => {
        const start = now + idx * 0.12;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(freq, start);
        gain.gain.setValueAtTime(0.001, start);
        gain.gain.exponentialRampToValueAtTime(0.045, start + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.11);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + 0.12);
      });
    } catch {
      // Ignore
    }
  }
}

export const callSounds = new CallSounds();