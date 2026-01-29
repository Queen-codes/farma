from fastapi import FastAPI, Form, UploadFile, File
from app.workflows.graph import farma_graph
import shutil
import os
from pathlib import Path

app = FastAPI(title="Farma API")

BASE_DIR = Path(__file__).resolve().parent.parent
TMP_DIR = BASE_DIR / "tmp_audio"
TMP_DIR.mkdir(exist_ok=True)


@app.get("/")
def root():
    return {"message": "Farma API", "status": "running"}


@app.post("/sms")
def receive_sms(From: str = Form(...), Body: str = Form(...)):
    """Receives SMS and processes through LangGraph workflow with Memory."""

    # Normalize to internal format
    sms_input = {
        "input_type": "sms",
        "phone": From,
        "message": Body,
        "audio_path": None,
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
    }

    print(f"INCOMING SMS from {From}")

    # Use Phone Number as Thread ID for Memory
    config = {"configurable": {"thread_id": From}}
    result = farma_graph.invoke(sms_input, config=config)

    print(f"PROCESSING COMPLETE")

    return {
        "status": result.get("status"),
        "intent": result.get("intent"),
        "language": result.get("language"),
        "parsed_data": result.get("parsed_data"),
        "farmer_response": result.get("farmer_response"),
    }


@app.post("/voice")
async def receive_voice(From: str = Form(...), audio_file: UploadFile = File(...)):
    """Receives Audio and processes through LangGraph workflow with Memory."""

    # Save file locally
    file_path = TMP_DIR / audio_file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(audio_file.file, buffer)

    # Normalize to internal format
    voice_input = {
        "input_type": "voice",
        "phone": From,
        "message": None,
        "audio_path": str(file_path),
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
    }

    print(f"INCOMING VOICE from {From}")

    # Use Phone Number as Thread ID for Memory
    config = {"configurable": {"thread_id": From}}
    result = farma_graph.invoke(voice_input, config=config)

    print(f"PROCESSING COMPLETE")

    return {
        "status": result.get("status"),
        "intent": result.get("intent"),
        "language": result.get("language"),
        "parsed_data": result.get("parsed_data"),
        "farmer_response": result.get("farmer_response"),
    }