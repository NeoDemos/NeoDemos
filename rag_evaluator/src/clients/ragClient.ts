import { Question, ModelAnswer, RetrievedChunk } from '../types';

/**
 * Wrapper for the NeoDemos RAG API.
 * 
 * TODO [USER CONFIGURATION]:
 * 1. Ensure `RAG_API_URL` points to your NeoDemos endpoint (e.g., http://localhost:8000/api/search).
 * 2. Update the `body` JSON structure to match what your API expects.
 * 3. Update the response mapping to correctly extract the answer and text chunks.
 */
export async function askRag(question: Question): Promise<ModelAnswer> {
    const apiUrl = process.env.RAG_API_URL || 'http://localhost:8000/api/search';
    const startTime = Date.now();

    try {
        // Construct GET URL with query parameters
        const url = new URL(apiUrl);
        url.searchParams.append('q', question.text);
        url.searchParams.append('mode', 'deep'); // Default to deep search for evaluation

        const response = await fetch(url.toString(), {
            method: 'GET',
            headers: { 'Accept': 'application/json' }
        });

        if (!response.ok) {
            throw new Error(`RAG API returned ${response.status} ${response.statusText}`);
        }

        const data = await response.json() as any;

        // Map response fields based on actual NeoDemos backend (ai_answer and sources)
        const answer = data.ai_answer || "No answer provided";

        // Extract sources (chunks) for judging
        const contextChunks: RetrievedChunk[] = (data.sources || []).map((src: any) => ({
            id: src.id || 'unknown',
            text: src.text || src.content || '',
            metadata: src.metadata || {}
        }));

        return {
            model: 'rag',
            answer,
            contextChunks,
            durationMs: Date.now() - startTime
        };
    } catch (error: any) {
        console.error(`[RAG Error] Question ${question.id}: ${error.message}`);
        return {
            model: 'rag',
            answer: '',
            error: error.message,
            durationMs: Date.now() - startTime
        };
    }
}
