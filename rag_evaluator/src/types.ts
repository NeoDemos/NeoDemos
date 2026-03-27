export interface Question {
    id: string;
    text: string;
    metadata?: Record<string, any>;
    goldAnswer?: string;
    goldContextIds?: string[];
}

export interface RetrievedChunk {
    id: string;
    text: string;
    score?: number;
    metadata?: Record<string, any>;
}

export interface ModelAnswer {
    model: 'rag' | 'gemini';
    answer: string;
    contextChunks?: RetrievedChunk[];
    error?: string;
    durationMs?: number;
}

export interface Scores {
    answer_relevance: number;      // 0-5
    faithfulness: number;          // 0-5
    factual_correctness: number;   // 0-5
}

export interface Judgment {
    questionId: string;
    ragScore: Scores;
    geminiScore: Scores;
    notes: string;
}
