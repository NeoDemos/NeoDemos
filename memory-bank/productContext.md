# Product Context

**Why this project exists**: 
City councillors face an overwhelming volume of documents and committee reports. Reading. understanding, and preparing for these meetings is time-consuming. NeoDemos solves this by providing AI-synthesized summaries, conflict extractions, and critical questions directly linked to the official agenda items. Its most unique feature, "Standpuntanalyse" (Party Lens Evaluation), further evaluates these items against the specific political ideology of a chosen party, allowing representatives to focus on what matters most for their constituents.

**Problems it solves**:
- Overwhelming document volume for local government officials.
- Difficulty in maintaining a consistent ideological stance across a myriad of complex policy proposals.
- Lack of immediate historical context when discussing recurring or evolving policy topics.

**How it works**:
- **Data Ingestion**: Periodically scrapes the Open Raadsinformatie API for meetings, agenda items, and linked PDF documents.
- **RAG & Chunking**: Runs continuous background jobs to compress and embed meeting document text into Qdrant for semantic search.
- **AI Analysis**: Uses Google Gemini 3 Flash to synthesize documents, retrieve historical context, and score the alignment of current proposals with party core values.
- **UI Presentation**: A responsive FastAPI/Jinja2 web interface presenting a calendar, meeting details, and expandable, real-time AI insights.
