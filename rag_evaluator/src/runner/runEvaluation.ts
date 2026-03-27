import * as dotenv from 'dotenv';
import * as path from 'path';

// Load .env at the earliest possible moment
dotenv.config({ path: path.join(__dirname, '../../.env') });
console.log(`[INIT] GEMINI_API_KEY loading: ${!!process.env.GEMINI_API_KEY}`);

import * as fs from 'fs';
import { loadQuestions } from '../testset/questionLoader';
import { askRag } from '../clients/ragClient';
import { askGemini } from '../clients/geminiClient';
import { judgeAnswers } from '../judge/judgeClient';

const TESTSET_PATH = process.env.TESTSET_PATH || path.join(__dirname, '../../data/questions.json');
const RESULTS_DIR = path.join(__dirname, '../../results');
const JSONL_OUT = path.join(RESULTS_DIR, 'results.jsonl');
const CSV_OUT = path.join(RESULTS_DIR, 'results.csv');

async function withRetry<T>(fn: () => Promise<T>, retries = 3, delayMs = 2000): Promise<T> {
    let lastError: any;
    for (let i = 0; i < retries; i++) {
        try {
            return await fn();
        } catch (e: any) {
            lastError = e;
            const isRateLimitOrServer = e.message?.includes('429') || e.message?.includes('50') || e.status === 429 || e.status >= 500;
            if (isRateLimitOrServer && i < retries - 1) {
                console.log(`[Retry] Fout opgetreden, wacht ${delayMs}ms voor retry ${i + 1}/${retries}...`);
                await new Promise(r => setTimeout(r, delayMs));
            } else {
                throw e;
            }
        }
    }
    throw lastError;
}

function escapeCsv(val: any): string {
    if (val === null || val === undefined) return '';
    const str = String(val);
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
        return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
}

function appendToCsv(filePath: string, result: any, includeHeader: boolean) {
    const headers = [
        'questionId', 'questionText',
        'ragAnswer', 'rag_relevance', 'rag_faithfulness', 'rag_correctness',
        'geminiAnswer', 'gemini_relevance', 'gemini_faithfulness', 'gemini_correctness',
        'judgeNotes'
    ];

    if (includeHeader) {
        fs.writeFileSync(filePath, headers.join(',') + '\n', 'utf8');
    }

    const row = [
        result.question.id,
        result.question.text,
        result.rag.answer, result.judgment.ragScore.answer_relevance, result.judgment.ragScore.faithfulness, result.judgment.ragScore.factual_correctness,
        result.gemini.answer, result.judgment.geminiScore.answer_relevance, result.judgment.geminiScore.faithfulness, result.judgment.geminiScore.factual_correctness,
        result.judgment.notes
    ];

    fs.appendFileSync(filePath, row.map(escapeCsv).join(',') + '\n', 'utf8');
}

async function main() {
    console.log('🚀 Start E2E RAG Evaluatie...');

    if (!fs.existsSync(RESULTS_DIR)) {
        fs.mkdirSync(RESULTS_DIR, { recursive: true });
    }

    if (fs.existsSync(JSONL_OUT)) fs.unlinkSync(JSONL_OUT);
    if (fs.existsSync(CSV_OUT)) fs.unlinkSync(CSV_OUT);

    const questions = loadQuestions(TESTSET_PATH);
    console.log(`📥 Geladen: ${questions.length} vragen uit ${TESTSET_PATH}\n`);

    let isFirstRow = true;

    for (let i = 0; i < questions.length; i++) {
        const q = questions[i];
        console.log(`\n--- Evaluatie Vraag ${i + 1}/${questions.length} [ID: ${q.id}] ---`);
        console.log(`Q: "${q.text}"`);

        console.log(`⏳ Fetching NeoDemos RAG Answer...`);
        const ragAns = await askRag(q);

        console.log(`⏳ Fetching Gemini 3 Flash Baseline...`);
        const geminiAns = await withRetry(() => askGemini(q));

        console.log(`⚖️  Judging Answers via LLM-as-a-Judge...`);
        const judgment = await withRetry(() => judgeAnswers(q, ragAns, geminiAns));

        const finalRecord = {
            timestamp: new Date().toISOString(),
            question: q,
            rag: ragAns,
            gemini: geminiAns,
            judgment: judgment
        };

        fs.appendFileSync(JSONL_OUT, JSON.stringify(finalRecord) + '\n', 'utf8');
        appendToCsv(CSV_OUT, finalRecord, isFirstRow);
        isFirstRow = false;

        console.log(`✅ Opgeslagen. [RAG Correctheid: ${judgment.ragScore?.factual_correctness || 0}/5] [Gemini Correctheid: ${judgment.geminiScore?.factual_correctness || 0}/5]`);
    }

    console.log(`\n🎉 Evaluatie voltooid! Rapport direct inzichtelijk in:\n- ${CSV_OUT}`);
}

main().catch(err => {
    console.error('❌ Evaluatie gefaald:', err);
    process.exit(1);
});
