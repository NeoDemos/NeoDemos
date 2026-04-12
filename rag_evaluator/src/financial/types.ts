/**
 * Financial benchmark types — WS2 Trustworthy Financial Analysis
 */

export interface FinancialQuestion {
    id: string;
    question: string;
    expected_eur: string | null;
    expected_eur_by_year?: Record<string, string>;
    expected_eur_gemeente?: string;
    expected_eur_derived_share?: string;
    expected_delta?: string;
    expected_programma: string;
    expected_sub_programma?: string;
    expected_jaar?: number;
    expected_jaren?: number[];
    expected_label?: string;
    expected_scope?: string;
    expected_entity_id?: string;
    expected_member_gemeente?: string;
    expected_member_gemeenten?: string[];
    source_doc: string;
    source_page?: number;
    accept_alternatives: string[];
    category: FinancialCategory;
    notes: string;
    placeholder?: boolean;
}

export type FinancialCategory =
    | 'single_lookup'
    | 'comparison'
    | 'sub_programma'
    | 'scope_grjr'
    | 'saldo'
    | 'narrative_guard';

export interface FinancialMatch {
    programma: string;
    sub_programma: string | null;
    jaar: number;
    bedrag_eur: string;
    label: string;
    scope: string;
    entity_id: string;
    source_pdf: string;
    page: number;
    table_cell_ref: string;
    document_id: string;
    aandeel_pct?: string;
    verification: {
        sha256: string;
        retrieved_at: string;
        method?: string;
    };
}

export interface ToolResponse {
    matches: FinancialMatch[];
    total: number;
    error?: string;
    hint?: string;
}

export interface ComparisonSeries {
    jaar: number;
    bedrag_eur: string | null;
    label: string;
    delta_abs: string | null;
    delta_pct: string | null;
}

export interface ComparisonResponse {
    programma: string;
    iv3_taakveld: string | null;
    series: Record<string, ComparisonSeries[]>;
    source_documents: string[];
    error?: string;
    hint?: string;
}

export interface QuestionResult {
    id: string;
    category: FinancialCategory;
    question: string;
    passed: boolean;
    expected: string | null;
    actual: string | null;
    details: string;
    durationMs: number;
    toolUsed: string;
    rawResponse?: any;
}

export interface BenchmarkSummary {
    timestamp: string;
    totalQuestions: number;
    totalPassed: number;
    totalFailed: number;
    passRate: string;
    categories: Record<FinancialCategory, {
        pass: number;
        fail: number;
        total: number;
    }>;
    results: QuestionResult[];
}
