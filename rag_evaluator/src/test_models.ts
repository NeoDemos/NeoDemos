import { GoogleGenAI } from '@google/genai';
import * as dotenv from 'dotenv';
import * as path from 'path';

dotenv.config({ path: path.join(__dirname, '../.env') });

async function listModels() {
    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
        console.error('No API key found in .env');
        return;
    }

    const genAI = new GoogleGenAI(apiKey);

    try {
        // Note: The @google/genai SDK doesn't have a direct listModels helper in some versions,
        // so we try a common model to see if it works, or we use a fetch call to the base API.
        console.log('Testing connection with gemini-pro...');
        const model = genAI.getGenerativeModel({ model: "gemini-pro" });
        const result = await model.generateContent("Hello?");
        console.log('Standard gemini-pro Response:', result.response.text());
    } catch (e: any) {
        console.error('Test with gemini-pro failed:', e.message);
    }

    try {
        console.log('Testing connection with gemini-1.5-flash...');
        const model = genAI.getGenerativeModel({ model: "gemini-1.5-flash" });
        const result = await model.generateContent("Hello?");
        console.log('Gemini 1.5 Flash Response:', result.response.text());
    } catch (e: any) {
        console.error('Test with gemini-1.5-flash failed:', e.message);
    }
}

listModels();
