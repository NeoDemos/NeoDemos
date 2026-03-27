# NeoDemos RAG Evaluator

Dit is het End-to-End (E2E) evaluatiesysteem voor de NeoDemos "Deep Research" RAG agent. Het vergelijkt de antwoorden van jouw lokaal geïmplementeerde RAG pijplijn tegen een ongetrainde *baseline* (Gemini 3 Flash) met behulp van *LLM-as-a-Judge* (Gemini Pro).

## Inrichten & Voorbereiden

1. **Installatie**: Ga naar deze map (`rag_evaluator`) in je terminal en installeer pakketten:
   ```bash
   npm install
   ```

2. **Configuratie**: 
   Kopieer `.env.example` naar `.env` en vul je gegevens in:
   ```bash
   cp .env.example .env
   # Gebruik 'nano .env' of VSCode om je GEMINI_API_KEY in te vullen
   ```

3. **Vragenlijst (Testset) Invullen**:
   Open `data/questions.json`. Hier kun je jouw testvragen kwijt. Een voorbeeld object:
   ```json
   {
      "id": "q1",
      "text": "Wat was de uiteindelijke kostenoverschrijding voor Boijmans?",
      "goldAnswer": "De kosten liepen op van €223M naar ruim €277M."
   }
   ```
   *Hoe meer vragen je specificeert mét een `goldAnswer`, des te nauwkeuriger de judge `factual_correctness` kan bepalen.*

4. **Je RAG-backend aanpassen (Optioneel)**:
   Als de API URL van NeoDemos verandert, pas dan de `RAG_API_URL` aan in je `.env`. De huidige client (`src/clients/ragClient.ts`) stuurt een JSON-body met `{ "query": "..." }` naar de search endpoint. Je kunt dit scrip tweaken als je backend iets anders verwacht (bijv. GET in plaats van POST).

## Evaluatie Runnen

Zorg dat je **hoofd NeroDemos backend / RAG API** draait in je andere terminal (`uvicorn main:app`).
Draai daarna de evaluatietest:

```bash
npm run eval
```

## Resultaten Analyseren
Zodra de run klaar is, worden de beoordelingen (Relevance, Faithfulness, Correctness) inclusief 'Judge Notes' opgeslagen in:
- `results/results.csv`: Perfect voor import in Excel of Google Sheets voor statistieken.
- `results/results.jsonl`: Handig voor programmatische analyse (elke regel is een JSON object met het volledige context verloop).
