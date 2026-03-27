import { GoogleGenAI } from '@google/genai';
import { Question, ModelAnswer } from '../types';

/**
 * Wrapper for a baseline Gemini model (e.g., Gemini 3 Flash).
 * This model answers purely based on its parametric knowledge 
 * without access to the local NeoDemos corpus.
 */
export async function askGemini(question: Question): Promise<ModelAnswer> {
    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey || apiKey.includes('REDACTED') || apiKey.includes('jouw-api-key')) {
        throw new Error(`Invalid or missing GEMINI_API_KEY in environment. Found: ${apiKey?.substring(0, 5)}...`);
    }

    const MODEL_NAME = process.env.GEMINI_BASELINE_MODEL || 'gemini-3-flash-preview';

    const ai = new GoogleGenAI({ apiKey });
    const startTime = Date.now();
    try {
        const prompt = `
Je bent een behulpzame, feitelijke assistent. 
Beantwoord de volgende vraag zo beknopt en feitelijk mogelijk op basis van je eigen kennis.
Verzin geen feiten als je het antwoord niet weet.

Vraag: ${question.text}
  `.trim();

        // New SDK structure for @google/genai
        const response = await ai.models.generateContent({
            model: `models/${MODEL_NAME}`,
            contents: [{ role: 'user', parts: [{ text: prompt }] }]
        });

        const responseText = response.text || '';

        return {
            model: 'gemini',
            answer: responseText,
            durationMs: Date.now() - startTime
        };
    } catch (error: any) {
        console.error(`[Gemini Error] Question ${question.id}: ${error.message}`);
        return {
            model: 'gemini',
            answer: '',
            error: error.message,
            durationMs: Date.now() - startTime
        };
    }
}


