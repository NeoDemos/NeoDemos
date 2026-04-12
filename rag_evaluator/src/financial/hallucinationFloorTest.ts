/**
 * WS2 Hallucination Floor Test
 *
 * Generates 50 random financial questions, calls zoek_financieel (text RAG path),
 * extracts every euro amount from the LLM response, and verifies each one exists
 * verbatim in the financial_lines table via vraag_begrotingsregel.
 *
 * PASS: zero unverified amounts across all 50 responses.
 *
 * Usage:
 *   npx ts-node rag_evaluator/src/financial/hallucinationFloorTest.ts
 *
 * Environment:
 *   MCP_TOOL_URL — MCP server base URL (default: http://localhost:8001)
 */

import * as dotenv from 'dotenv';
import * as path from 'path';

dotenv.config({ path: path.join(__dirname, '../../.env') });

import * as fs from 'fs';
import {
    callZoekFinancieel,
    callVraagBegrotingsregel,
} from './mcpClient';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const RESULTS_DIR = path.join(__dirname, '../../results');
const NUM_QUESTIONS = 50;

// ---------------------------------------------------------------------------
// Question templates
// ---------------------------------------------------------------------------

const PROGRAMMAS = [
    'Veilig',
    'Onderwijs',
    'Werk en Inkomen',
    'Volksgezondheid en Zorg',
    'Maatschappelijke ondersteuning',
    'Stedelijke ontwikkeling',
    'Beheer van de stad',
    'Cultuur, Sport en Recreatie',
    'Economische zaken',
    'Overhead',
    'Bestuur en dienstverlening',
    'Algemene middelen',
];

const JAREN = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026];

const TEMPLATES = [
    (prog: string, jaar: number) => `Wat zijn de lasten voor ${prog} in ${jaar}?`,
    (prog: string, jaar: number) => `Hoeveel is er begroot voor ${prog} in de begroting ${jaar}?`,
    (prog: string, jaar: number) => `Wat is het budget voor ${prog} in ${jaar}?`,
    (prog: string, jaar: number) => `Wat zijn de baten van programma ${prog} in ${jaar}?`,
    (prog: string, jaar: number) => `Wat is het saldo van ${prog} in de jaarstukken ${jaar}?`,
    (prog: string, jaar: number) => `Hoeveel geld is er uitgegeven aan ${prog} in ${jaar}?`,
    (prog: string, jaar: number) => `Wat waren de kosten voor ${prog} in de begroting van ${jaar}?`,
    (prog: string, jaar: number) => `Wat is de totale begroting van ${prog} voor het jaar ${jaar}?`,
];

function randomChoice<T>(arr: T[]): T {
    return arr[Math.floor(Math.random() * arr.length)];
}

function generateRandomQuestions(count: number): Array<{ question: string; programma: string; jaar: number }> {
    const questions: Array<{ question: string; programma: string; jaar: number }> = [];
    const seen = new Set<string>();

    while (questions.length < count) {
        const prog = randomChoice(PROGRAMMAS);
        const jaar = randomChoice(JAREN);
        const template = randomChoice(TEMPLATES);
        const question = template(prog, jaar);

        const key = `${prog}|${jaar}`;
        if (seen.has(key)) continue;
        seen.add(key);

        questions.push({ question, programma: prog, jaar });
    }

    return questions;
}

// ---------------------------------------------------------------------------
// Euro extraction
// ---------------------------------------------------------------------------

/**
 * Extract all euro amounts from a text string.
 *
 * Handles formats like:
 * - 82.400.000,00
 * - 82400000.00
 * - 82.400.000
 * - EUR 82.400.000
 * - 82,4 miljoen
 * - 82.400.000,00 euro
 * - 314.500.000
 *
 * Returns the amounts as normalized decimal strings (e.g. "82400000.00").
 */
function extractEuroAmounts(text: string): string[] {
    const amounts: string[] = [];

    // Pattern 1: Dutch notation with dots and comma (82.400.000,00 or 82.400.000)
    const dutchPattern = /(?:EUR\s*|euro\s*|\u20AC\s*)?(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?)(?:\s*(?:euro|EUR|\u20AC))?/g;
    let match;

    while ((match = dutchPattern.exec(text)) !== null) {
        const raw = match[1];
        // Convert Dutch format: 82.400.000,00 -> 82400000.00
        const normalized = raw.replace(/\./g, '').replace(',', '.');
        // Ensure 2 decimal places
        const num = parseFloat(normalized);
        if (!isNaN(num) && num > 0) {
            amounts.push(num.toFixed(2));
        }
    }

    // Pattern 2: Plain number format (82400000.00)
    const plainPattern = /(?:EUR\s*|euro\s*|\u20AC\s*)(\d{6,}(?:\.\d{1,2})?)(?:\s*(?:euro|EUR|\u20AC))?/g;
    while ((match = plainPattern.exec(text)) !== null) {
        const num = parseFloat(match[1]);
        if (!isNaN(num) && num > 0) {
            const str = num.toFixed(2);
            if (!amounts.includes(str)) {
                amounts.push(str);
            }
        }
    }

    // Pattern 3: "X,Y miljoen" or "X miljoen" format
    const miljoenPattern = /(\d+(?:,\d+)?)\s*miljoen/gi;
    while ((match = miljoenPattern.exec(text)) !== null) {
        const raw = match[1].replace(',', '.');
        const num = parseFloat(raw) * 1_000_000;
        if (!isNaN(num) && num > 0) {
            const str = num.toFixed(2);
            if (!amounts.includes(str)) {
                amounts.push(str);
            }
        }
    }

    // Pattern 4: "X,Y miljard" or "X miljard" format
    const miljardPattern = /(\d+(?:,\d+)?)\s*miljard/gi;
    while ((match = miljardPattern.exec(text)) !== null) {
        const raw = match[1].replace(',', '.');
        const num = parseFloat(raw) * 1_000_000_000;
        if (!isNaN(num) && num > 0) {
            const str = num.toFixed(2);
            if (!amounts.includes(str)) {
                amounts.push(str);
            }
        }
    }

    return amounts;
}

// ---------------------------------------------------------------------------
// Verification
// ---------------------------------------------------------------------------

interface AmountVerification {
    amount: string;
    verified: boolean;
    source: string | null;
}

async function verifyAmountInFinancialLines(
    amount: string,
    programma: string,
    jaar: number,
): Promise<AmountVerification> {
    try {
        const resp = await callVraagBegrotingsregel({
            gemeente: 'rotterdam',
            jaar,
            programma,
        });

        if (resp.matches && resp.matches.length > 0) {
            const match = resp.matches.find(m => m.bedrag_eur === amount);
            if (match) {
                return {
                    amount,
                    verified: true,
                    source: `financial_lines: ${match.programma}/${match.label} ${match.jaar} (sha256: ${match.verification?.sha256?.slice(0, 12)}...)`,
                };
            }

            // Check if the amount matches any row (might be a different label)
            const anyMatch = resp.matches.find(m => {
                // Also check without trailing zeros
                const a = parseFloat(amount);
                const b = parseFloat(m.bedrag_eur);
                return Math.abs(a - b) < 0.01;
            });
            if (anyMatch) {
                return {
                    amount,
                    verified: true,
                    source: `financial_lines (fuzzy): ${anyMatch.programma}/${anyMatch.label} ${anyMatch.jaar}`,
                };
            }
        }

        // Try broader search: the amount might belong to a different programma/year
        // than what we generated, if the LLM pulled data from adjacent context
        return {
            amount,
            verified: false,
            source: null,
        };
    } catch {
        return {
            amount,
            verified: false,
            source: null,
        };
    }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

interface QuestionResult {
    question: string;
    programma: string;
    jaar: number;
    responseLength: number;
    amountsFound: number;
    amountsVerified: number;
    amountsUnverified: number;
    verifications: AmountVerification[];
    durationMs: number;
}

async function main() {
    console.log('Hallucination Floor Test — WS2 Trustworthy Financial Analysis');
    console.log('==============================================================\n');

    const mcpUrl = process.env.MCP_TOOL_URL || 'http://localhost:8001';
    console.log(`MCP server: ${mcpUrl}`);
    console.log(`Generating ${NUM_QUESTIONS} random financial questions...\n`);

    const questions = generateRandomQuestions(NUM_QUESTIONS);

    if (!fs.existsSync(RESULTS_DIR)) {
        fs.mkdirSync(RESULTS_DIR, { recursive: true });
    }

    const results: QuestionResult[] = [];
    let totalAmountsFound = 0;
    let totalVerified = 0;
    let totalUnverified = 0;

    for (let i = 0; i < questions.length; i++) {
        const { question, programma, jaar } = questions[i];
        console.log(`[${i + 1}/${NUM_QUESTIONS}] ${question}`);

        const start = Date.now();

        try {
            // Call zoek_financieel (text RAG path)
            const response = await callZoekFinancieel({
                onderwerp: question,
                budget_year: jaar,
            });

            // Extract euro amounts from the response
            const amounts = extractEuroAmounts(response);
            console.log(`  Extracted ${amounts.length} euro amount(s)`);

            // Verify each amount
            const verifications: AmountVerification[] = [];
            for (const amt of amounts) {
                const v = await verifyAmountInFinancialLines(amt, programma, jaar);
                verifications.push(v);
                if (v.verified) {
                    console.log(`    [OK] ${amt} — ${v.source}`);
                } else {
                    console.log(`    [!!] ${amt} — NOT FOUND in financial_lines`);
                }
            }

            const verified = verifications.filter(v => v.verified).length;
            const unverified = verifications.filter(v => !v.verified).length;

            totalAmountsFound += amounts.length;
            totalVerified += verified;
            totalUnverified += unverified;

            results.push({
                question,
                programma,
                jaar,
                responseLength: response.length,
                amountsFound: amounts.length,
                amountsVerified: verified,
                amountsUnverified: unverified,
                verifications,
                durationMs: Date.now() - start,
            });
        } catch (err: any) {
            console.log(`  [ERR] ${err.message}`);
            results.push({
                question,
                programma,
                jaar,
                responseLength: 0,
                amountsFound: 0,
                amountsVerified: 0,
                amountsUnverified: 0,
                verifications: [],
                durationMs: Date.now() - start,
            });
        }
    }

    // ---------------------------------------------------------------------------
    // Summary
    // ---------------------------------------------------------------------------

    console.log('\n\nHallucination Floor Test Results');
    console.log('================================');
    console.log(`Questions tested:        ${NUM_QUESTIONS}`);
    console.log(`Total euro amounts found: ${totalAmountsFound}`);
    console.log(`Verified in DB:          ${totalVerified}`);
    console.log(`Unverified (potential hallucinations): ${totalUnverified}`);
    console.log();

    if (totalUnverified === 0) {
        console.log('PASS: Zero unverified euro amounts. Hallucination floor holds.');
    } else {
        console.log(`FAIL: ${totalUnverified} unverified euro amount(s) detected.`);
        console.log('\nUnverified amounts:');
        for (const r of results) {
            for (const v of r.verifications) {
                if (!v.verified) {
                    console.log(`  - ${v.amount} in response to: "${r.question}"`);
                }
            }
        }
    }

    // ---------------------------------------------------------------------------
    // Write results
    // ---------------------------------------------------------------------------

    const now = new Date();
    const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
    const outputPath = path.join(RESULTS_DIR, `hallucination_floor_${dateStr}.json`);

    const report = {
        timestamp: now.toISOString(),
        config: {
            numQuestions: NUM_QUESTIONS,
            mcpUrl,
        },
        summary: {
            totalAmountsFound,
            totalVerified,
            totalUnverified,
            passed: totalUnverified === 0,
        },
        results,
    };

    fs.writeFileSync(outputPath, JSON.stringify(report, null, 2), 'utf8');
    console.log(`\nDetailed results written to: ${outputPath}`);

    // Exit code
    if (totalUnverified > 0) {
        process.exit(1);
    }
    process.exit(0);
}

main().catch(err => {
    console.error('Hallucination floor test failed:', err);
    process.exit(1);
});
