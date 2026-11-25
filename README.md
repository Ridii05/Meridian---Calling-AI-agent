# Meridian — Voice AI Doctor Appointment Assistant

Meridian is a real-time, browser-based Voice AI clinic front-desk system. Callers speak naturally over WebRTC to an AI receptionist that checks physician schedules, books appointments, and cancels existing bookings—identifying callers strictly by the phone number they speak aloud, with live synchronization to a clinical operations dashboard.

---

## Table of Contents
1. [System Overview](#system-overview)
2. [Architecture & Audio Pipeline](#architecture--audio-pipeline)
3. [Technology Stack](#technology-stack)
4. [Key Engineering Decisions](#key-engineering-decisions)
5. [Project Structure](#project-structure)
6. [Setup & Local Installation](#setup--local-installation)
7. [Demo Flows & Testing](#demo-flows--testing)
8. [API & Event Reference](#api--event-reference)

---

## System Overview

Meridian solves end-to-end phone appointment scheduling without logins, OTPs, or manual form entry:

* **Voice-First Caller Identification:** The agent identifies callers solely by the phone number they speak aloud during the conversation and enforces a mandatory digit-by-digit read-back confirmation before executing any booking or cancellation.
* **Multi-Action Single-Call Flow:** Within a single continuous voice call, a caller can query doctor/specialty availability, book an open slot, or cancel an existing appointment.
* **Tactile Phone Call Interface:** Clicking **Try Call** opens a mobile-handset overlay with synthesized Web Audio ringback/connect/hang-up tones, live call timer, mute control, a dual-channel frequency waveform reacting to both caller and agent audio, live interim + finalized speech bubbles, and an animated confirmation card when a booking or cancellation completes.
* **Real-Time Front-Desk Dashboard:** The underlying clinical desk displays physicians, date-tabbed availability slots, an appointments ledger, and a live transcript—updating instantaneously via Server-Sent Events (SSE) with row/slot highlight animations whenever the voice agent modifies the database.

---

## Architecture & Audio Pipeline

```text
┌─────────────────────────────┐         WebRTC (Opus Audio + ICE)         ┌─────────────────────────────────────────┐
│     React Front-Desk UI     │ ◄───────────────────────────────────────► │      FastAPI + Pipecat Voice Server     │
│                             │                                           │                                         │
│  • Phone Handset Overlay    │         Server-Sent Events (SSE)          │  SmallWebRTCTransport (in/out)          │
│  • Web Audio Waveform       │ ◄───────────────────────────────────────  │    │                                    │
│  • Physician Slot Grid      │   (transcripts, tool activity, bookings)  │    ▼                                    │
│  • Appointments Ledger      │                                           │  ElevenLabs STT (scribe_v2)             │
└─────────────────────────────┘         REST API (/api/doctors, etc.)     │    │                                    │
                                ◄───────────────────────────────────────► │    ▼                                    │
                                                                          │  Silero VAD + Smart Turn v3 + Context   │
                                                                          │    │                                    │
                                                                          │    ▼                                    │
                                                                          │  Google Gemini LLM (Function Calling)   │
                                                                          │    ├── check_doctor_availability()      │
                                                                          │    ├── book_appointment()  ──► SQLite   │
                                                                          │    └── cancel_appointment() ─► SQLite   │
                                                                          │    │                                    │
                                                                          │    ▼                                    │
                                                                          │  ElevenLabs TTS (eleven_flash_v2_5)     │
                                                                          └─────────────────────────────────────────┘