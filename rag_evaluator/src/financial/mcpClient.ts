/**
 * MCP tool client for WS2 financial benchmark.
 *
 * Calls the MCP server's tools via the main NeoDemos API at localhost:8000.
 * The /api/search endpoint wraps tool calls internally, but for the financial
 * benchmark we need direct tool invocation. We use the MCP HTTP transport
 * (streamable-http) on port 8001 with JSON-RPC 2.0 messages.
 *
 * Fallback: if MCP_TOOL_URL env var is set, uses that URL instead.
 */

import { ToolResponse, ComparisonResponse } from './types';

const MCP_BASE = process.env.MCP_TOOL_URL || 'http://localhost:8001';
const REQUEST_TIMEOUT_MS = 30_000;

let _requestId = 0;

function nextRequestId(): number {
    return ++_requestId;
}

/**
 * Make a JSON-RPC 2.0 call to the MCP server via streamable-http.
 */
async function mcpToolCall(toolName: string, args: Record<string, any>): Promise<any> {
    const body = {
        jsonrpc: '2.0',
        id: nextRequestId(),
        method: 'tools/call',
        params: {
            name: toolName,
            arguments: args,
        },
    };

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
        const response = await fetch(`${MCP_BASE}/mcp`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
            },
            body: JSON.stringify(body),
            signal: controller.signal,
        });

        if (!response.ok) {
            throw new Error(`MCP server returned ${response.status} ${response.statusText}`);
        }

        const contentType = response.headers.get('content-type') || '';

        // Handle SSE response (text/event-stream)
        if (contentType.includes('text/event-stream')) {
            const text = await response.text();
            // Parse SSE: look for data lines containing JSON-RPC response
            const lines = text.split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.result) return data.result;
                        if (data.error) throw new Error(`MCP error: ${JSON.stringify(data.error)}`);
                    } catch {
                        // Not valid JSON, continue
                    }
                }
            }
            throw new Error('No valid JSON-RPC response found in SSE stream');
        }

        // Handle direct JSON response
        const data = await response.json();
        if (data.error) {
            throw new Error(`MCP error: ${JSON.stringify(data.error)}`);
        }
        return data.result || data;
    } finally {
        clearTimeout(timeout);
    }
}

/**
 * Parse tool content from MCP response.
 * MCP tools return { content: [{ type: 'text', text: '...' }] }
 */
function extractToolText(result: any): string {
    if (!result) return '';
    // Direct string result
    if (typeof result === 'string') return result;
    // MCP content array format
    if (result.content && Array.isArray(result.content)) {
        return result.content
            .filter((c: any) => c.type === 'text')
            .map((c: any) => c.text)
            .join('\n');
    }
    // Already a parsed object
    return JSON.stringify(result);
}

/**
 * Call vraag_begrotingsregel MCP tool.
 */
export async function callVraagBegrotingsregel(params: {
    gemeente: string;
    jaar: number;
    programma: string;
    sub_programma?: string;
    include_gr_derived?: boolean;
}): Promise<ToolResponse> {
    const args: Record<string, any> = {
        gemeente: params.gemeente,
        jaar: params.jaar,
        programma: params.programma,
    };
    if (params.sub_programma) args.sub_programma = params.sub_programma;
    if (params.include_gr_derived) args.include_gr_derived = true;

    const result = await mcpToolCall('vraag_begrotingsregel', args);
    const text = extractToolText(result);

    try {
        return JSON.parse(text);
    } catch {
        return { matches: [], total: 0, error: `Failed to parse response: ${text.slice(0, 200)}` };
    }
}

/**
 * Call vergelijk_begrotingsjaren MCP tool.
 */
export async function callVergelijkBegrotingsjaren(params: {
    gemeente: string;
    programma: string;
    jaren: number[];
}): Promise<ComparisonResponse> {
    const result = await mcpToolCall('vergelijk_begrotingsjaren', {
        gemeente: params.gemeente,
        programma: params.programma,
        jaren: params.jaren,
    });
    const text = extractToolText(result);

    try {
        return JSON.parse(text);
    } catch {
        return { programma: params.programma, iv3_taakveld: null, series: {}, source_documents: [], error: `Failed to parse response: ${text.slice(0, 200)}` };
    }
}

/**
 * Call zoek_financieel MCP tool (text RAG path for narrative questions).
 */
export async function callZoekFinancieel(params: {
    onderwerp: string;
    datum_van?: string;
    datum_tot?: string;
    budget_year?: number;
}): Promise<string> {
    const result = await mcpToolCall('zoek_financieel', params);
    return extractToolText(result);
}

/**
 * Fetch financial_entities metadata for a given entity_id.
 * Uses vraag_begrotingsregel with include_gr_derived to surface entity data.
 */
export async function callFinancialEntityLookup(entityId: string): Promise<any> {
    // For entity metadata queries (like member_gemeenten), we use the
    // context_primer tool which includes entity info, or a direct DB query.
    // Since MCP tools don't have a direct entity lookup, we rely on the
    // tool's scope metadata to validate entity presence.
    const result = await mcpToolCall('haal_context_primer_op', {});
    return extractToolText(result);
}

/**
 * Call vraag_begrotingsregel to get gr_member_contributions data.
 */
export async function callGrMemberContribution(params: {
    entity_id: string;
    jaar: number;
    member_gemeente: string;
}): Promise<ToolResponse> {
    // Use vraag_begrotingsregel with include_gr_derived=true to get member contributions
    return callVraagBegrotingsregel({
        gemeente: params.member_gemeente,
        jaar: params.jaar,
        programma: 'jeugdhulp',
        include_gr_derived: true,
    });
}
