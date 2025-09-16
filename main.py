import os
import json
import base64
import asyncio
import logging
import datetime
from logging.handlers import RotatingFileHandler
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect
from dotenv import load_dotenv

load_dotenv()

# Configure logging
LOG_DIR = os.getenv('LOG_DIR', 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# Create logger
logger = logging.getLogger("call_assistant")
logger.setLevel(logging.INFO)

# Create handlers
log_file = os.path.join(LOG_DIR, f"call_assistant_{datetime.datetime.now().strftime('%Y%m%d')}.log")
file_handler = RotatingFileHandler(log_file, maxBytes=10485760, backupCount=10)  # 10MB per file, keep 10 files
console_handler = logging.StreamHandler()

# Create formatters
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Add formatters to handlers
file_handler.setFormatter(file_formatter)
console_handler.setFormatter(console_formatter)

# Add handlers to logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = (
    "あなたは株式会社こくぶ商会のタイヤ回収受付担当です。"
    "あなたの仕事は廃タイヤの回収依頼を受け付けることです。"
    "早口な対応を心がけてください。"
    "会話の流れ："
    "1. 「こくぶ商会タイヤ回収受付です」と挨拶します。"
    "2. まず「まず御社名とご担当者のお名前を教えていただけますでしょうか」と確認します。"
    "（ただし聞き取れなかった場合は聞き返すように）"
    "3. 次に「タイヤの種類（乗用車かトラックか）」「タイヤの本数」をお伺いします。"
    "（ただし聞き取れなかった場合は聞き返すように）"
    "4. 次に「その他ご要望をお聞かせください」と確認します。"
    "（日程変更のご相談、回収状況のお問い合わせ、回収時間のご質問等にも対応します）"
    "5. 次に「回収ご希望の日時はございますか？なお、日程によってはご希望に添えない場合もございますが、担当者から調整のご連絡をいたします。」と確認します。"
    "（ただし聞き取れなかった場合は聞き返すように）"
    "6. 最後に「ありがとうございます。担当者から折り返しご連絡いたします。」と伝えて会話を終了します。"
    "その他、お客様のご要望に応じて適切な対応をしてください。"
)
VOICE = 'coral'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    logger.info("Server index page accessed")
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    logger.info("Incoming call received")
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    # response.say("Please wait while we connect your call to the A. I. voice assistant, powered by Twilio and the Open-A.I. Realtime API")
    # response.pause(length=1)
    # response.say("Please wait while we connect your call to ex nova's voice assistant AI")
    # response.pause(length=7)
    # response.say("O.K. you can start talking!")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    logger.info("New client connected to media stream")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2025-06-03',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        session_id = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{id(websocket)}"
        logger.info(f"Created session: {session_id}")
        await initialize_session(openai_ws)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        logger.info(f"Incoming stream started - SID: {stream_sid}, Session: {session_id}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                logger.info(f"Client disconnected - Session: {session_id}")
                if openai_ws.open:
                    await openai_ws.close()
            except Exception as e:
                logger.error(f"Error in receive_from_twilio: {e} - Session: {session_id}")

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    logger.info(f"Raw OpenAI message received: {response} - Session: {session_id}")

                    if response['type'] in LOG_EVENT_TYPES:
                        if response['type'] == 'response.content.done':
                            # Log the full text response
                            if 'message' in response and 'content' in response['message']:
                                content_texts = [item['text'] for item in response['message']['content'] if item['type'] == 'text']
                                response_text = " ".join(content_texts)
                                logger.info(f"AI Response: {response_text} - Session: {session_id}")
                        
                        logger.debug(f"OpenAI event: {response['type']} - Session: {session_id}")

                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                logger.debug(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms - Session: {session_id}")

                        # Update last_assistant_item safely
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        await send_mark(websocket, stream_sid)

                    # Trigger an interruption. Your use case might work better using `input_audio_buffer.speech_stopped`, or combining the two.
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        logger.info(f"Speech started detected - Session: {session_id}")
                        if last_assistant_item:
                            logger.info(f"Interrupting response with id: {last_assistant_item} - Session: {session_id}")
                            await handle_speech_started_event()
                            
                    # Log user's input when transcription is available
                    if response.get('type') == 'response.content.part' and 'message' in response:
                        if 'content' in response['message']:
                            for content in response['message']['content']:
                                if content.get('type') == 'text' and content.get('text'):
                                    logger.info(f"User said: {content['text']} - Session: {session_id}")
            except Exception as e:
                logger.error(f"Error in send_to_twilio: {e} - Session: {session_id}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            logger.debug(f"Handling speech started event - Session: {session_id}")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    logger.debug(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms - Session: {session_id}")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        logger.debug(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms - Session: {session_id}")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        try:
            await asyncio.gather(receive_from_twilio(), send_to_twilio())
        except Exception as e:
            logger.error(f"Error in WebSocket connection: {e} - Session: {session_id}")
        finally:
            logger.info(f"Session ended: {session_id}")

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    logger.info("Adding a pause before the initial conversation prompt")
    await asyncio.sleep(1)  # Added 2-second pause here
    logger.info("Sending initial conversation prompt to OpenAI")
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "最初に「こくぶ商会タイヤ回収受付です。まず御社名とご担当者のお名前を教えていただけますでしょうか」と聞いてください。"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    logger.info("Initializing OpenAI session")
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "semantic_vad",
                "eagerness": "medium",
                "create_response": True,
                "interrupt_response": True,
            },
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.6,
        }
    }
    logger.debug('Sending session update to OpenAI')
    await openai_ws.send(json.dumps(session_update))

    # Uncomment the next line to have the AI speak first
    await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
