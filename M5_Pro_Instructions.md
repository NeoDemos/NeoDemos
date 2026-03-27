# M5 Pro Transcription Instructions

This document outlines how to use your new Macbook M5 Pro to process the remaining historical committee meetings with maximum quality. 

## Background
Currently, we have downloaded the raw audio formats (MP4/HLS/MP3) for thousands of meetings from 2019-2022 to the `downloads/` directory. We stopped transcribing them on the old machine because we were artificially limited to the `whisper-tiny` model, causing severe phonetic hallucinations (e.g. "likie die tijd straatio").

With the M5 Pro, you have the unified memory available to run **Whisper Large v3** or **Whisper Turbo** locally, which will produce near-perfect Dutch transcripts.

## Steps for the M5 Pro

### 1. Transfer the Files
- Transfer the entire `NeoDemos/downloads/` directory to the M5 Pro.
- Transfer the `pipeline_state_*.json` files so the new machine knows what has been completed.

### 2. Update the Transcription Logic
- Open `pipeline/extractor.py` on the M5 Pro.
- Navigate to the `WhisperTranscriber` class (around line 245).
- Change the `model_name` attribute:
  ```python
  # Change THIS:
  self.model_name = "mlx-community/whisper-tiny-mlx"
  
  # To THIS (Absolute best quality, runs smoothly on 64GB M5 Pro):
  self.model_name = "mlx-community/whisper-large-v3-mlx" 
  ```

### 3. Run the "Local Transcription" Script
We will need a script that downloads the audio/video file, passes it through the M5 Pro's Whisper model immediately, and deletes the video file after processing to save space. Run this process **file-by-file**.

*(Note: When you boot up the M5 Pro, you can ask the Assistant to "Write a script to process the 2018-2022 meetings file-by-file using the pipeline and update the database", and it will generate the necessary loop.)*

### 4. Database Merge
- If the M5 Pro is running the database locally, the data will be ingested directly.
- If the database remains on a remote server/different machine, ensure your `.env` connection string (`DATABASE_URL`) points to the correct remote PostgreSQL instance before running the ingestion.

## Summary
Due to space constraints on the old machine, the M5 Pro will handle both the **Downloading** and **AI Transcription** phases file-by-file. This guarantees zero phonetic hallucinations in the final Knowledge Graph.

## Local Infrastructure Setup
The NeoDemos web application and pipeline require a local database and vector store setup.

### 1. PostgreSQL (Relational Data)
- **Version**: PostgreSQL 16
- **Setup**: You can run it via the provided `docker-compose.yml` or a local installation.
- **Database**: `neodemos`
- **Credentials**: See your `.env` file (Default: `postgres`/`postgres`).
- **Transfer**: If you have existing data, consider a `pg_dump` or migrate the volumes.

### 2. Qdrant (Vector Search)
- **Service**: Required for the search API (runs on port `6333`).
- **Pipeline Mode**: The ingestion script (`pipeline/ingestion.py`) writes directly to the folder `./data/qdrant_storage`.
- **Transfer**: **CRITICAL** - Ensure you transfer the entire `./data/qdrant_storage` directory to the M5 Pro. This contains all your embedded chunks.
- **Server**: For the web search to work, run a Qdrant container:
  ```bash
  docker run -p 6333:6333 -v $(pwd)/data/qdrant_storage:/qdrant/storage qdrant/qdrant
  ```

### 3. Environment Variables
Ensure your `.env` on the M5 Pro points to `localhost`:
```env
DB_HOST=localhost
DB_PORT=5432
# GEMINI_API_KEY=...
```
