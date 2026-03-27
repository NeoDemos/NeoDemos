import { GoogleGenAI } from '@google/genai';
import { Question, ModelAnswer, Judgment, Scores } from '../types';

/**
 * Uses a powerful Judge model (e.g. Gemini Pro) to evaluate the generated answers.
 */
export async function judgeAnswers(
    question: Question,
    ragAnswer: ModelAnswer,
    geminiAnswer: ModelAnswer
): Promise<Judgment> {
    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
        throw new Error('GEMINI_API_KEY environment variable is missing.');
    }

    const MODEL_NAME = process.env.JUDGE_MODEL || 'gemini-1.5-pro';

    const ai = new GoogleGenAI({ apiKey });

    // Format context chunks for the prompt
    const contextText = (ragAnswer.contextChunks || [])
        .map((c, i) => `[Chunk ${i + 1} - ID: ${c.id}]:\n${c.text}`)
        .join('\n\n');

    const prompt = `
Je bent een strenge, onafhankelijke AI Judge.
Je taak is om twee gegenereerde antwoorden te beoordelen op een gebruikersvraag over de Rotterdamse gemeentepolitiek.
Je geeft scores tussen 0 en 5 (waarbij 5 het beste is).

Vraag: 
${question.text}

${question.goldAnswer ? `\nGold Answer (Referentie Antwoord):\n${question.goldAnswer}\n` : ''}

=== Beschikbare Context (Aangeleverd door RAG systeem) ===
${contextText || "Geen context aangeleverd."}

=== Antwoord 1 (RAG Systeem) ===
${ragAnswer.answer}

=== Antwoord 2 (Gemini Baseline) ===
${geminiAnswer.answer}

=== Instructies voor Beoordeling ===
Oordeel op de volgende 3 criteria per model (0-5 score):
1. answer_relevance: Biedt het antwoord direct antwoord op de vraag zonder te veel onnodige informatie? (5 = perfect relevant)
2. faithfulness (Contextuele Integriteit):
   - Voor RAG: Is het antwoord GEBASEERD op de meegeleverde context? 
   - MAAK ONDERSCHEID: 
     - Als een feit klopt maar NIET in de context staat, is de faithfulness score LAGER (omdat het niet uit de bron komt), maar de factual_correctness is HOOG.
     - Als een feit NIET in de context staat en de AI zegt "Ik weet het niet", scoor dan HOOG op faithfulness (eerlijkheid).
     - Als de AI informatie verzint die nergens staat (hallucinatie), scoor dan 0.
3. factual_correctness: Hoe feitelijk juist is het antwoord? 
   - Gebruik het 'Gold Answer' als de absolute bron van waarheid indien aanwezig.
   - Indien Gold Answer afwezig is: Gebruik je eigen kennis van Rotterdamse politiek/projecten (zoals Feyenoord City, Schiekadeblok, etc.).

Je MOET strikte JSON retourneren die exact voldoet aan dit schema:
{
  "ragScore": { "answer_relevance": getal, "faithfulness": getal, "factual_correctness": getal },
  "geminiScore": { "answer_relevance": getal, "faithfulness": getal, "factual_correctness": getal },
  "notes": "Jouw toelichting van max 6 zinnen. Geef specifiek aan of RAG feiten noemt die NIET in de context stonden maar wel klopten (external knowledge test)."
}
  `.trim();

    try {
        const response = await ai.models.generateContent({
            model: `models/${MODEL_NAME}`,
            contents: [{ role: 'user', parts: [{ text: prompt }] }],
            config: {
                temperature: 0.1,
                responseMimeType: "application/json"
            }
        });

        const outputText = response.text || "{}";

        // Clean up potential markdown blocks if present despite instructions
        const cleanJsonStr = outputText.replace(/^^\\s*```[a-z]*|```\\s*$/gm, '').trim();

        const parsed = JSON.parse(cleanJsonStr);

        return {
            questionId: question.id,
            ragScore: parseScores(parsed.ragScore),
            geminiScore: parseScores(parsed.geminiScore),
            notes: parsed.notes || "No notes provided."
        };

    } catch (error: any) {
        console.error(`[Judge Error] Question ${question.id}: ${error.message}`);
        return {
            questionId: question.id,
            ragScore: { answer_relevance: 0, faithfulness: 0, factual_correctness: 0 },
            geminiScore: { answer_relevance: 0, faithfulness: 0, factual_correctness: 0 },
            notes: `Judge evaluation failed: ${error.message}`
        };
    }
}

function parseScores(scoreObj: any): Scores {
    return {
        answer_relevance: Number(scoreObj?.answer_relevance) || 0,
        faithfulness: Number(scoreObj?.faithfulness) || 0,
        factual_correctness: Number(scoreObj?.factual_correctness) || 0,
    };
}
