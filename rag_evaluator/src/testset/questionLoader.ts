import * as fs from 'fs';
import * as path from 'path';
import { Question } from '../types';

export function loadQuestions(filePath: string): Question[] {
    try {
        const rawData = fs.readFileSync(path.resolve(filePath), 'utf8');
        const questions: Question[] = JSON.parse(rawData);

        // Basic validation
        if (!Array.isArray(questions)) {
            throw new Error('Testset JSON must be an array of Question objects.');
        }

        questions.forEach((q, index) => {
            if (!q.id) throw new Error(`Question at index ${index} is missing an 'id'.`);
            if (!q.text) throw new Error(`Question at index ${index} is missing 'text'.`);
        });

        return questions;
    } catch (error) {
        console.error(`Error loading testset from ${filePath}:`, error);
        process.exit(1);
    }
}
