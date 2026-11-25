import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Phone, RotateCcw } from "lucide-react";
import PhoneModal from "./PhoneModal";
import { useVoiceCall } from "./useVoiceCall";

function formatShortDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((dt - today) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return dt.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function formatTime12h(hhmm) {
  const [hStr, mStr] = hhmm.split(":");
  const h = Number(hStr);
  const suffix = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${mStr} ${suffix}`;
}

export default function App() {
  const [doctors, setDoctors] = useState([]);
  const [appointments, setAppointments] = useState([]);
  const [selectedDate, setSelectedDate] = useState("");
  const [transcript, setTranscript] = useState([]);
  const [interimText, setInterimText] = useState("");
  const [phoneOpen, setPhoneOpen] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const [activeTool, setActiveTool] = useState(null);
  const [confirmationCard, setConfirmationCard] = useState(null);
  const [highlightedApt, setHighlightedApt] = useState(null); // { id, kind }
  const [highlightedSlot, setHighlightedSlot] = useState(null); // { id, kind }

  const {
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
  } = useVoiceCall();

  const fetchDashboardData = useCallback(async () => {
    try {
      const [docRes, aptRes] = await Promise.all([
        fetch("/api/doctors"),
        fetch("/api/appointments"),
      ]);
      const docJson = await docRes.json();
      const aptJson = await aptRes.json();
      setDoctors(docJson.doctors || []);
      setAppointments(aptJson.appointments || []);

      if (docJson.doctors?.length && !selectedDate) {
        const firstDate = docJson.doctors[0].slots?.[0]?.date;
        if (firstDate) setSelectedDate(firstDate);
      }
    } catch {
      // Backend may still be starting
    }
  }, [selectedDate]);

  useEffect(() => {
    fetchDashboardData();
  }, [fetchDashboardData]);

  // Real-time SSE listener for transcripts, tool activity, bookings, and cancellations
  useEffect(() => {
    const es = new EventSource("/api/events");

    es.onopen = () => setSseConnected(true);
    es.onerror = () => setSseConnected(false);

    es.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        const { type, payload } = msg;

        if (type === "transcript_interim") {
          setInterimText(payload.text || "");
        } else if (type === "transcript") {
          setInterimText("");
          setTranscript((prev) => [...prev, payload]);
        } else if (type === "tool_call") {
          const labels = {
            check_doctor_availability: "Checking schedule…",
            book_appointment: "Booking slot…",
            cancel_appointment: "Cancelling appointment…",
          };
          setActiveTool(labels[payload.tool] || payload.tool);
          setTimeout(() => setActiveTool(null), 2200);
        } else if (type === "appointment_booked") {
          fetchDashboardData();
          setSelectedDate(payload.slot_date);
          setConfirmationCard({ ...payload, kind: "booked" });
          setHighlightedApt({ id: payload.id, kind: "booked" });
          setHighlightedSlot({ id: payload.slot_id, kind: "book" });
          setTimeout(() => setHighlightedApt(null), 3000);
          setTimeout(() => setHighlightedSlot(null), 2000);
        } else if (type === "appointment_cancelled") {
          fetchDashboardData();
          setConfirmationCard({ ...payload, kind: "cancelled" });
          setHighlightedApt({ id: payload.id, kind: "cancelled" });
          setHighlightedSlot({ id: payload.slot_id, kind: "free" });
          setTimeout(() => setHighlightedApt(null), 3000);
          setTimeout(() => setHighlightedSlot(null), 2000);
        } else if (type === "data_reset") {
          fetchDashboardData();
        }
      } catch {
        // Ignore non-JSON keepalives
      }
    };

    return () => es.close();
  }, [fetchDashboardData]);

  const availableDates = useMemo(() => {
    const set = new Set();
    doctors.forEach((d) => d.slots.forEach((s) => set.add(s.date)));
    return Array.from(set).sort();
  }, [doctors]);

  const openSlotsCount = useMemo(() => {
    return doctors.reduce(
      (sum, d) => sum + d.slots.filter((s) => !s.is_booked).length,
      0
    );
  }, [doctors]);

  const confirmedCount = useMemo(
    () => appointments.filter((a) => a.status === "confirmed").length,
    [appointments]
  );

  const handleTryCall = () => {
    setPhoneOpen(true);
    if (callState === "idle" || callState === "ended") {
      setTranscript([]);
      setInterimText("");
      setConfirmationCard(null);
      startCall();
    }
  };

  const handleCloseModal = () => {
    if (callState === "ended") {
      resetCallState();
    }
    setPhoneOpen(false);
  };

  const handleResetDemo = async () => {
    await fetch("/api/reset", { method: "POST" });
    setTranscript([]);
    setInterimText("");
    setConfirmationCard(null);
  };

  return (
    <div className="shell">
      {/* Top Bar */}
      <header className="topbar">
        <div className="brand">
          <h1 className="brand-name serif">Meridian</h1>
          <span className="brand-divider" />
          <span className="brand-sub">Clinic Front Desk</span>
        </div>

        <div className="topbar-actions">
          <div className="status-chip">
            <span className={`dot ${sseConnected ? "live" : ""}`} />
            <span>{sseConnected ? "Desk Live" : "Connecting"}</span>
          </div>

          <button
            className="btn btn-ghost"
            onClick={handleResetDemo}
            title="Reset mock schedule"
          >
            <RotateCcw size={14} />
            <span>Reset</span>
          </button>

          <button
            className={`btn btn-primary ${callState === "connected" ? "active-call" : ""}`}
            onClick={handleTryCall}
          >
            <Phone size={14} />
            <span>
              {callState === "connected"
                ? "Active Call"
                : callState === "calling"
                ? "Calling…"
                : "Try Call"}
            </span>
          </button>
        </div>
      </header>

      {/* Compact Metric Strip */}
      <section className="metrics">
        <div className="metric-card">
          <span className="metric-label">Physicians</span>
          <span className="metric-val serif">{doctors.length}</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Open Slots</span>
          <span className="metric-val serif">{openSlotsCount}</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Confirmed</span>
          <span className="metric-val serif">{confirmedCount}</span>
        </div>
      </section>

      {/* Main Two-Column Desk */}
      <main className="grid">
        {/* Left: Physicians & Slot Grid */}
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">Physicians & Availability</h2>
            <div className="date-tabs">
              {availableDates.map((d) => (
                <button
                  key={d}
                  className={`date-tab ${selectedDate === d ? "active" : ""}`}
                  onClick={() => setSelectedDate(d)}
                >
                  {formatShortDate(d)}
                </button>
              ))}
            </div>
          </div>

          <div className="doctor-list">
            {doctors.map((doc) => {
              const daySlots = doc.slots.filter((s) => s.date === selectedDate);
              return (
                <div key={doc.id} className="doctor-row">
                  <div className="doctor-meta">
                    <div>
                      <span className="doctor-name">{doc.name}</span>
                      <span className="doctor-spec">{doc.specialty}</span>
                    </div>
                    <span className="doctor-room mono">{doc.room}</span>
                  </div>

                  <div className="slot-grid">
                    {daySlots.map((slot) => {
                      const flashClass =
                        highlightedSlot?.id === slot.id
                          ? highlightedSlot.kind === "book"
                            ? "flash-book"
                            : "flash-free"
                          : "";
                      return (
                        <span
                          key={slot.id}
                          className={`slot-pill mono ${
                            slot.is_booked ? "booked" : ""
                          } ${flashClass}`}
                        >
                          {formatTime12h(slot.time)}
                        </span>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Right: Appointments Ledger + Desk Transcript */}
        <div>
          <section className="panel">
            <div className="panel-head">
              <h2 className="panel-title">Appointments</h2>
              <span className="mono" style={{ fontSize: 12, color: "var(--ink-faint)" }}>
                {appointments.length} total
              </span>
            </div>

            <div className="ledger-list">
              {appointments.length === 0 ? (
                <div className="empty-state">No appointments yet.</div>
              ) : (
                appointments.map((apt) => {
                  const rowHighlight =
                    highlightedApt?.id === apt.id
                      ? highlightedApt.kind === "booked"
                        ? "highlight-booked"
                        : "highlight-cancelled"
                      : "";
                  return (
                    <div key={apt.id} className={`apt-row ${rowHighlight}`}>
                      <div className="apt-main">
                        <div className="apt-doc">
                          <span>{apt.doctor_name}</span>
                          <span
                            className="mono"
                            style={{ fontSize: 11, color: "var(--ink-faint)" }}
                          >
                            {apt.id}
                          </span>
                        </div>
                        <div className="apt-sub mono">
                          <span>{formatShortDate(apt.slot_date)}</span>
                          <span>·</span>
                          <span>{formatTime12h(apt.slot_time)}</span>
                          <span>·</span>
                          <span>{apt.caller_phone_display}</span>
                        </div>
                      </div>

                      <span
                        className={`badge ${
                          apt.status === "confirmed"
                            ? "badge-confirmed"
                            : "badge-cancelled"
                        }`}
                      >
                        {apt.status === "confirmed" ? "Confirmed" : "Cancelled"}
                      </span>
                    </div>
                  );
                })
              )}
            </div>
          </section>

          {/* Desk-level Live Transcript */}
          <section className="panel desk-transcript">
            <div className="panel-head">
              <h2 className="panel-title">Live Transcript</h2>
              {transcript.length > 0 && (
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-faint)" }}>
                  {transcript.length} turns
                </span>
              )}
            </div>

            {transcript.length === 0 ? (
              <div className="empty-state">
                Start a call to view the live conversation.
              </div>
            ) : (
              <div className="transcript-body">
                {transcript.map((line, i) => (
                  <div key={i} className="t-line">
                    <span className={`t-role mono ${line.role}`}>
                      {line.role === "agent" ? "Desk" : "Caller"}
                    </span>
                    <span>{line.text}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </main>

      {/* Phone-Style Call Screen */}
      <PhoneModal
        open={phoneOpen}
        callState={callState}
        isMuted={isMuted}
        elapsedSeconds={elapsedSeconds}
        micLevel={micLevel}
        error={error}
        analyserRef={analyserRef}
        transcript={transcript}
        interimText={interimText}
        activeTool={activeTool}
        confirmationCard={confirmationCard}
        onToggleMute={toggleMute}
        onEndCall={endCall}
        onStartCall={() => {
          setTranscript([]);
          setInterimText("");
          setConfirmationCard(null);
          startCall();
        }}
        onClose={handleCloseModal}
      />
    </div>
  );
}