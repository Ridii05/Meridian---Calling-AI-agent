import os
import time
from datetime import date, timedelta
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMRunFrame,
    TTSSpeakFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
import aiohttp
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.services.elevenlabs.stt import (
    CommitStrategy,
    ElevenLabsRealtimeSTTService,
    ElevenLabsSTTService,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

# Handle both Pipecat 1.3+ (PipelineWorker/WorkerRunner) and 1.0-1.2 (PipelineTask/PipelineRunner)
try:
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker as PipelineExecutor
    from pipecat.pipeline.runner import WorkerRunner as ExecutorRunner
except ImportError:
    from pipecat.pipeline.task import PipelineParams, PipelineTask as PipelineExecutor
    from pipecat.pipeline.runner import PipelineRunner as ExecutorRunner

# Handle FunctionSchema / ToolsSchema path across Pipecat 1.x releases
try:
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
    from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
except ImportError:
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

import db
from events import event_bus


def build_system_prompt() -> str:
    today = date.today()
    tomorrow = today + timedelta(days=1)
    day_after = today + timedelta(days=2)

    return f"""You are the front-desk receptionist at Meridian Clinic, answering live phone calls.
Today's date is {today.isoformat()} ({today.strftime('%A')}).
Tomorrow is {tomorrow.isoformat()} ({tomorrow.strftime('%A')}).
The day after tomorrow is {day_after.isoformat()} ({day_after.strftime('%A')}).

Available doctors at Meridian Clinic:
- Dr. Ananya Rao (Cardiology)
- Dr. Vikram Mehta (Dermatology)
- Dr. Priya Nair (Pediatrics)
- Dr. Rohan Kulkarni (Orthopedics)

Strict operational rules:
1. Voice style: Speak naturally, warmly, and concisely in plain spoken English. Keep replies to 1 to 3 short sentences. Never use bullet points, markdown, asterisks, or emojis.
2. Caller identity: Callers are identified ONLY by the phone number they say aloud. Never ask for an email, login, or OTP.
3. Mandatory phone read-back: Before calling book_appointment OR cancel_appointment, you MUST read the caller's phone number back to them clearly (grouped like 9-8-7, 6-5-4, 3-2-1-0) and wait for them to explicitly confirm it is correct.
4. Checking availability: Always call check_doctor_availability before quoting open times. Offer 2 to 3 convenient slots rather than reading a long list. Convert 24-hour times into natural speech (e.g. "2:00 PM" instead of "14:00").
5. End-to-end flow: You can check availability, book, and cancel in the same call. After booking, state the doctor, date, time, and booking ID briefly.
6. Opening greeting: When the call starts, greet the caller briefly: "Meridian Clinic front desk, how can I help you today?"
"""


def build_tools() -> ToolsSchema:
    check_availability_fn = FunctionSchema(
        name="check_doctor_availability",
        description="Check open appointment slots for a doctor, specialty, and/or date.",
        properties={
            "doctor_name": {
                "type": "string",
                "description": "Doctor's name (e.g., 'Dr. Ananya Rao' or 'Rao'). Optional.",
            },
            "specialty": {
                "type": "string",
                "description": "Medical specialty (Cardiology, Dermatology, Pediatrics, Orthopedics). Optional.",
            },
            "date": {
                "type": "string",
                "description": "Date in YYYY-MM-DD format, or 'today' / 'tomorrow'. Optional.",
            },
        },
        required=[],
    )

    book_appointment_fn = FunctionSchema(
        name="book_appointment",
        description=(
            "Book a specific open appointment slot for the caller. "
            "ONLY call this AFTER reading the phone number back to the caller and getting confirmation."
        ),
        properties={
            "caller_phone": {
                "type": "string",
                "description": "The caller's confirmed phone number (digits).",
            },
            "doctor_name": {
                "type": "string",
                "description": "Full or last name of the doctor.",
            },
            "date": {
                "type": "string",
                "description": "Appointment date in YYYY-MM-DD format.",
            },
            "time_slot": {
                "type": "string",
                "description": "Slot start time (e.g., '14:00' or '2:00 PM').",
            },
            "caller_name": {
                "type": "string",
                "description": "Caller's name if they mentioned it. Optional.",
            },
        },
        required=["caller_phone", "doctor_name", "date", "time_slot"],
    )

    cancel_appointment_fn = FunctionSchema(
        name="cancel_appointment",
        description=(
            "Cancel an existing confirmed appointment found by the caller's phone number. "
            "ONLY call this AFTER reading the phone number back and getting confirmation."
        ),
        properties={
            "caller_phone": {
                "type": "string",
                "description": "The caller's confirmed phone number (digits).",
            },
            "appointment_id": {
                "type": "string",
                "description": "Specific appointment ID (e.g., 'APT-101') if the caller has multiple bookings. Optional.",
            },
        },
        required=["caller_phone"],
    )

    return ToolsSchema(
        standard_tools=[
            check_availability_fn,
            book_appointment_fn,
            cancel_appointment_fn,
        ]
    )


async def run_voice_pipeline(webrtc_connection: SmallWebRTCConnection) -> None:
    """Runs one isolated Pipecat voice session for an incoming browser WebRTC call."""
    call_id = webrtc_connection.pc_id
    greeting_until = 0.0
    logger.info(f"[{call_id}] Starting Meridian voice pipeline")

    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_voice = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    google_key = os.getenv("GOOGLE_API_KEY", "")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    stt_mode = os.getenv("ELEVENLABS_STT_MODE", "realtime").lower()

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
        ),
    )

    async with aiohttp.ClientSession() as http_session:
        # 1. ElevenLabs STT (Realtime WebSocket Scribe v2, or HTTP segmented fallback)
        if stt_mode == "http":
            stt = ElevenLabsSTTService(
                api_key=elevenlabs_key,
                aiohttp_session=http_session,
                settings=ElevenLabsSTTService.Settings(
                    model="scribe_v2",
                    language="en",
                ),
            )
        else:
            stt = ElevenLabsRealtimeSTTService(
                api_key=elevenlabs_key,
                commit_strategy=CommitStrategy.MANUAL,
                settings=ElevenLabsRealtimeSTTService.Settings(
                    model="scribe_v2_realtime",
                    language="en",
                ),
            )

        # 2. Google Gemini LLM with tool calling (Pipecat 1.17+ system_instruction)
        llm = GoogleLLMService(
            api_key=google_key,
            settings=GoogleLLMService.Settings(
                model=gemini_model,
                system_instruction=build_system_prompt(),
            ),
        )

        # 3. ElevenLabs streaming WebSocket TTS
        tts = ElevenLabsTTSService(
            api_key=elevenlabs_key,
            sample_rate=24000,
            settings=ElevenLabsTTSService.Settings(
                voice=elevenlabs_voice,
                model="eleven_flash_v2_5",
                stability=0.55,
                similarity_boost=0.78,
            ),
        )

        # --- Register the 3 required tools ---
        async def handle_check_availability(params: FunctionCallParams):
            args = params.arguments or {}
            logger.info(f"[{call_id}] Tool check_doctor_availability({args})")
            await event_bus.publish(
                "tool_call",
                {"call_id": call_id, "tool": "check_doctor_availability", "args": args},
            )
            result = db.check_doctor_availability(
                doctor_name=args.get("doctor_name"),
                specialty=args.get("specialty"),
                target_date=args.get("date"),
            )
            await params.result_callback(result)

        async def handle_book_appointment(params: FunctionCallParams):
            args = params.arguments or {}
            logger.info(f"[{call_id}] Tool book_appointment({args})")
            await event_bus.publish(
                "tool_call",
                {"call_id": call_id, "tool": "book_appointment", "args": args},
            )
            result = db.book_appointment(
                caller_phone=args.get("caller_phone", ""),
                doctor_name=args.get("doctor_name", ""),
                target_date=args.get("date", ""),
                time_slot=args.get("time_slot", ""),
                caller_name=args.get("caller_name"),
            )
            if result.get("ok"):
                await event_bus.publish("appointment_booked", result["appointment"])
            await params.result_callback(result)

        async def handle_cancel_appointment(params: FunctionCallParams):
            args = params.arguments or {}
            logger.info(f"[{call_id}] Tool cancel_appointment({args})")
            await event_bus.publish(
                "tool_call",
                {"call_id": call_id, "tool": "cancel_appointment", "args": args},
            )
            result = db.cancel_appointment(
                caller_phone=args.get("caller_phone", ""),
                appointment_id=args.get("appointment_id"),
            )
            if result.get("ok"):
                await event_bus.publish("appointment_cancelled", result["cancelled"])
            await params.result_callback(result)

        llm.register_function("check_doctor_availability", handle_check_availability)
        llm.register_function("book_appointment", handle_book_appointment)
        llm.register_function("cancel_appointment", handle_cancel_appointment)

        # 4. Universal LLMContext + Silero VAD wired into LLMUserAggregatorParams (Pipecat 1.0+)
        context = LLMContext(
            messages=[],
            tools=build_tools(),
        )

        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        confidence=0.7,
                        start_secs=0.2,
                        stop_secs=0.2,
                        min_volume=0.7,
                    )
                )
            ),
        )

        # Forward clean finalized turns to the frontend live transcript over SSE
        @user_aggregator.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(*args):
            message = args[-1]
            text = getattr(message, "content", "") or ""
            if text.strip():
                await event_bus.publish(
                    "transcript",
                    {
                        "call_id": call_id,
                        "role": "caller",
                        "text": text.strip(),
                    },
                )

        @assistant_aggregator.event_handler("on_assistant_turn_stopped")
        async def on_assistant_turn_stopped(*args):
            message = args[-1]
            text = getattr(message, "content", "") or ""
            if text.strip():
                await event_bus.publish(
                    "transcript",
                    {
                        "call_id": call_id,
                        "role": "agent",
                        "text": text.strip(),
                    },
                )

        class TranscriptTap(FrameProcessor):
            async def process_frame(self, frame, direction: FrameDirection):
                await super().process_frame(frame, direction)
                if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
                    if time.monotonic() < greeting_until:
                        return
                    text = (frame.text or "").strip()
                    if not text or (text.startswith("[") and text.endswith("]")):
                        return
                    if isinstance(frame, TranscriptionFrame):
                        logger.info(f"[{call_id}] STT heard: {text}")
                    await event_bus.publish(
                        "transcript_interim",
                        {"call_id": call_id, "text": text},
                    )
                await self.push_frame(frame, direction)

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                TranscriptTap(),
                user_aggregator,
                llm,
                tts,
                transport.output(),
                assistant_aggregator,
            ]
        )

        worker = PipelineExecutor(
            pipeline,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(_transport, _connection):
            nonlocal greeting_until
            logger.info(f"[{call_id}] Browser WebRTC client connected")
            await event_bus.publish("call_state", {"call_id": call_id, "state": "connected"})
            greeting = "Meridian Clinic front desk, how can I help you today?"
            greeting_until = time.monotonic() + 3.2
            await event_bus.publish(
                "transcript",
                {"call_id": call_id, "role": "agent", "text": greeting},
            )
            await worker.queue_frames([TTSSpeakFrame(greeting, append_to_context=True)])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport, _connection):
            logger.info(f"[{call_id}] Browser WebRTC client disconnected")
            await event_bus.publish("call_state", {"call_id": call_id, "state": "ended"})
            await worker.cancel()

        runner = ExecutorRunner(handle_sigint=False)
        await runner.run(worker)