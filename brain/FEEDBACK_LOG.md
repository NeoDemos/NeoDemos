# MCP Feedback Log

Use this file to capture observations while testing. Paste the query, what came back, and what was wrong or missing.

**Triage flow** *(updated 2026-04-11)*: Dennis drops raw observations here → **weekly triage (Mondays)** converts each entry into one of:

- (a) **Memory update** — if the learning is about the user, the project, or long-term feedback
- (b) **WS handoff edit** in [`docs/handoffs/`](../docs/handoffs/) — if the fix/feature has a clear workstream home (preferred default)
- (c) **[`TODOS.md`](../TODOS.md) triage-inbox entry** — only if no clear WS home exists and the item needs follow-up; max 7 days before it gets a WS home or is discarded
- (d) **Discard** — if the observation turned out to be wrong, stale, or already covered

Each triaged entry gets a `**Triaged:** YYYY-MM-DD → [destination]` line appended at the bottom of its entry, so the audit trail stays visible without cluttering the raw log.

See [`TODOS.md`](../TODOS.md) header for the full scope rules on where items live.

---

## Template

```
### [DATE] [TOOL] — [one-line description]

**Query / params:**
(paste what you sent)

**What came back:**
(paste or summarise the response)

**What was wrong / missing:**
(your observation)

**Severity:** low / medium / high
```

---

## Log

<!-- Add entries below, newest at top -->

### 2026-04-14 Systematic MCP testing — onderwijs / sociale huur + nachtleven / stemgedrag (two sessions)

**Query / params:**
Two consecutive testing sessions via Claude.ai iOS app against the live MCP surface. Session 1 themed around onderwijs, sociale huurwoningen, D66 Kasmi, budget comparison across 10 tools. Session 2 themed around D66 nachtlevenmoties + party voting behaviour on restrictive proposals. Full raw feedback pasted in conversation 2026-04-14.

**What came back / what was wrong:**
10 numbered items in session 1 (BUG-001..BUG-007 + IMP-001..IMP-006), 6 items + 2 observations + 1 feature request in session 2. Condensed list below; full detail lives in the handoff entries they've been triaged into.

- **BUG-001** `vergelijk_partijen` returns zero results (known pre-Phase A expected behaviour, not a new bug)
- **BUG-002** `tijdlijn_besluitvorming` relevance floor too low — returns procedural noise at 0.71-0.73
- **BUG-003** `lijst_vergaderingen` commissie filter only matches abbreviation codes (WIOS, ZOCS), not full names
- **BUG-004** `haal_partijstandpunt_op` profile DB empty; fallback RAG returns 2015-2017 fragments
- **BUG-005 / BUG-002 s2** `zoek_moties` wastes slots on "Lijst met openstaande moties" overview docs
- **BUG-006 / BUG-006 s2** `zoek_moties` `uitkomst` unreliable — inherits from container, misses `AANGENOMEN` in body
- **BUG-007** `lijst_vergaderingen` iBabs/ORI duplicates (numeric ID + UUID for same meeting)
- **BUG-003 s2** `zoek_moties` no BB-number dedup across tussenberichten + herziene versies
- **IMP-001** Financial tools return `"87506.00"` without unit — Rotterdam publishes in thousands
- **IMP-002** `iv3_omschrijving` null — reference table has the name, just needs JOIN
- **IMP-003** `zoek_uitspraken` party-filter weak (returns generic chunks, not speaker-level)
- **IMP-004** `zoek_uitspraken_op_rol` dominated by signatures + toezeggingen rows
- **IMP-005** `lees_fragment` returns 1 chunk when 3 requested with no visibility on total
- **IMP-006** Chunk titles generated in English ("D66's Clarification of Stance")
- **FEATURE-001** `zoek_stemgedrag` + `motie_stemmen` table needed for "how did D66 vote on others' proposals"

**Observations (positive):**
- `vergelijk_begrotingsjaren` + `vraag_begrotingsregel` return rich, verified financial data (only unit-label issue)
- `zoek_uitspraken` + `zoek_uitspraken_op_rol` partij filter works for finding relevant debate contributions (Walgenbach 2019-2020 nachtcultuur)
- Offensive/defensive distinction confirmed: MCP covers the "what D66 filed" side well; defensive side ("how D66 voted on others") is the gap and justifies `motie_stemmen` as #1 v0.3.0 priority

**Severity:** mixed — HIGH for BUG-002, BUG-005, BUG-006; MEDIUM for BUG-003, BUG-004, BUG-007; LOW for IMP-*

**Triaged:** 2026-04-14 → multiple destinations:
- **[WS4 §(4) Tool quality fixes from 2026-04-14](../docs/handoffs/WS4_MCP_DISCIPLINE.md#4-tool-quality-fixes-from-2026-04-14-systematic-testing-added-2026-04-14)** (10 items T1–T10: BUG-002, BUG-003, BUG-005, BUG-006, BUG-003 s2, IMP-003, IMP-004, IMP-005 + broad-query demotion). Ship before v0.2.0 eval gate.
- **[WS14 B5 (meeting dedup)](../docs/handoffs/WS14_CALENDAR_QUALITY.md)** — BUG-007 confirmed, direction set (prefer record with populated `commissie` code; surface both IDs in response).
- **[WS2b IV-1 + IV-2](../docs/handoffs/WS2b_IV3_TAAKVELD.md#appended-2026-04-14-from-mcp-testing-feedback)** — IMP-001 (unit field) + IMP-002 (iv3_omschrijving JOIN). Ships with main WS2b backfill.
- **[WS1 Future work](../docs/handoffs/WS1_GRAPHRAG.md#future-work-do-not-do-in-this-workstream)** — BUG-001 is expected pre-Phase A (no action); full `haal_partijstandpunt_op` programme-based profile seeding deferred past v0.4; `vergelijk_partijen` retrieval-based flavour stays in v0.2.0.
- **[WS6 Follow-up bug](../docs/handoffs/WS6_SUMMARIZATION.md#follow-up-bug-from-2026-04-14-mcp-testing)** — IMP-006 English chunk titles.
- **[WS15 `motie_stemmen` (NEW, v0.2.0)](../docs/handoffs/WS15_MOTIE_STEMMEN.md)** — FEATURE-001 `zoek_stemgedrag` + `motie_stemmen` table promoted to v0.2.0 on 2026-04-14 (was v0.3.0). Regex-parsed from 1,077 besluitenlijsten, no LLM. Defensive-vote query class (D66 voting on others' restrictive nachtleven motions) is now a v0.2.0 acceptance item.

---

### 2026-04-11 Full session audit — Woningbouwbeleid Rotterdam (10 jaar)

**Query / params:**
Onderzoekssessie via NeoDemos MCP tools. Vier stappen: (1) overzicht woningbouwbeleid 10 jaar, (2) tijdlijn + visualisatie, (3) zelfevaluatie hallucinations, (4) coalitiesamenstelling + Tweebosbuurt-verdieping.

**What came back:**
Breed maar deels onbetrouwbaar resultaat. Drie parallelle `zoek_raadshistorie` calls gaven goede breedte. Motieresultaten waren traceerbaar (aangenomen/verworpen + document_id). Stap-3 zelfevaluatie was eerlijk maar reactief.

**What was wrong / missing:**

🔴 **Fout 1 — Coalitie 2022–2026 gepresenteerd zonder verificatie.** "Leefbaar, VVD, D66, CDA" werd als feit in de tijdlijn gezet zonder bron. Juiste werkwijze: nooit coalitiesamenstelling presenteren zonder direct `zoek_uitspraken_op_rol` / document_id.

🔴 **Fout 2 — Tweebosbuurt-framing structureel verkeerd.** Oorspronkelijke framing suggereerde dat GL en PvdA als oppositie "tegen stemden", terwijl ze coalitiepartij waren en vóór hun eigen beleid stemden. Dit verdraait de politieke werkelijkheid — ernstiger dan een feitelijk detailfoutje.

🟡 **Fout 3 — Datum coalitieakkoord 2018 verkeerd in tijdlijn.** "Nov 2018" geplaatst voor coalitieakkoord, dat was juni/mei 2018. November 2018 was de Tweebosbuurt-vergadering — twee gebeurtenissen samengeklonterd.

🟡 **Fout 4 — Kerncijfers niet geleverd.** Oorspronkelijke vraag vroeg expliciet om jaarlijkse aantallen sociale huurwoningen. Gat omzeild met beleidsdata in plaats van direct en prominent benoemen dat de cijfers niet uit de beschikbare bronnen kwamen.

**Structurele tekortkomingen:**
- Te weinig `lees_fragment` calls — `zoek_raadshistorie` snippets werden als primaire bron gebruikt zonder doorzetten naar volledige context
- Zelfevaluatie was reactief (stap 3) in plaats van proactief (had al in stap 1 gemoeten vóór tijdlijn-opname)
- Parallelle searches werden niet synthetisch afgewogen — conflicterende snippets bleven naast elkaar staan

**Concrete verbeteringen / protocol voor volgende sessies:**
1. Elke claim zonder traceerbaar document_id markeren als `[NIET BEVESTIGD]` vóór visualisatie/samenvatting
2. `lees_fragment` als **standaard** tweede stap na elke relevante `zoek_raadshistorie` hit
3. Coalitie/partijsamenstelling altijd via `zoek_uitspraken_op_rol`, nooit uit algemene kennis
4. Onbeantwoorde deelvragen als **eerste** alinea benoemen, niet na drie alinea's beleid
5. Bij stemmingsanalyse: eerst coalitie-/oppositiestatus vaststellen vóór duiding (voorkomt Tweebosbuurt-type fouten)

**Prioriteit:** 🔴 1) Geen coalitiesamenstelling zonder document_id · 🔴 2) Coalitiestatus vóór stemmingsduiding · 🟡 3) `lees_fragment` standaard · 🟡 4) Onbeantwoorde kerncijfers direct benoemen · 🟢 5) Proactieve zelfevaluatie

**Severity:** high — raakt kernbetrouwbaarheid van civic-intelligence output; twee fouten (coalitie + Tweebosbuurt-framing) zouden in productie politiek misleidend zijn.

---

### 2026-04-11 zoek_uitspraken_op_rol — Wethouderswisseling Karremans → Lansink Bastemeijer (VVD, RTHA)

**Query / params:**
Verificatievraag: wanneer was Karremans wethouder, en wie volgde hem op? Directe aanleiding: LLM had in een eerder antwoord "Karremans was wethouder tot de verkiezingen van 2026" gesuggereerd (zonder tool gebruik).

**What came back:**
Correct en gestructureerd:
- Karremans: raadslid 29-03-2018 → 01-09-2021; wethouder 02-09-2021 → **02-07-2024**
- Lansink Bastemeijer: wethouder 11-07-2024 → heden
- Wisseling halverwege coalitieperiode 2022–2026 (niet bij verkiezingen)

**Wat werkte goed:**
- Tool retourneerde exacte datumaanvang per rol — exact het soort feitelijke ankerpunt dat een LLM zonder tool mist of hallucineert
- Correcte bevestiging dat de wisseling mid-coalition was, niet na verkiezingen
- Automatisch filteren per rolperiode (raadslid vs wethouder) is een sterk mechanisme voor uitspraken zonder contextvervuiling

**Aandachtspunten:**

**1. Overlappende raadslid-entries Lansink Bastemeijer**
Twee raadslid-periodes: 29-03-2018 → 16-06-2022 **en** 16-03-2022 → 18-03-2026. Overlap van ~3 maanden (maart–juni 2022). Waarschijnlijk legitiem — in Nederlandse gemeentepolitiek wordt de nieuwe raad geïnstalleerd in maart na verkiezingen terwijl de oude raad tot een latere datum formeel doorloopt. Maar het kan ook een dubbele ingest-entry zijn.
- **Actie:** controleer of dit patroon breder voorkomt bij raadsleden die herkozen worden. Als het structureel is, is het geen bug maar een registratiefeit; als het alleen bij enkele personen voorkomt, is het een data-artefact.

**2. LLM hallucineert politieke rol-data — data-fix, geen instructie-fix**
Het "Karremans tot 2026"-antwoord laat zien dat LLMs politieke personeelsdata schatten op basis van training data. Dit is een ontbrekende-data-probleem: de LLM had de huidige bezetting niet in context.
- **Actie (WS4):** `wethouders`-array toevoegen aan `get_neodemos_context()` primer (huidig bezetting per portefeuille, gegenereerd uit `persons_roles` bij server boot). De LLM krijgt de juiste facts bij sessiestart — geen "roep dit proactief aan"-instructie in de tool-description nodig. Zie [WS4_MCP_DISCIPLINE.md §Context primer](../docs/handoffs/WS4_MCP_DISCIPLINE.md).

**Severity:** low — tool werkt correct. Twee kleine aandachtspunten: data-kwaliteitscheck op overlappende entries, en tool-description preventieve-aanroep instructie.

**Triaged:** 2026-04-11 → (a) WS4 §Context primer: `wethouders`-array toevoegen aan `get_neodemos_context()` — data-fix voor rol-hallucination, geen instructie-fix; (b) data-kwaliteitscheck overlappende raadslidperiodes (WS5a §data integrity audit, lage prioriteit).

---

### 2026-04-11 scan_breed / zoek_uitspraken — Haven & Duurzaamheid

**Query / params:**
Vraag: verduurzaming haven Rotterdam, beleidsdoelen, scheepvaart, waterstof, windenergie. Tool sequence: `scan_breed` (40 resultaten) → `zoek_raadshistorie` → `zoek_uitspraken` → `lees_fragment`.

**What came back:**
Inhoudelijk goed — klimaatmijlpalen (25%/55%/2050), kolencentrales, windenergie 350 MW, waterstofproject, verworpen motie 24bb003669 (mei 2024, GroenLinks/BIJ1/PvdD/50Plus/PvdA over scheepvaartverduurzaming, LNG-kritiek, lock-in-effect). Wethouder Simons correct als portefeuillehouder haven.

**Wat werkte goed:**
- `lees_fragment` correct: 8 fragmenten uit klimaatactieplan (doc 6115020), 2 uit motie 24bb003669 inclusief uitslag "VERWORPEN"
- Politieke positie per partij correct geattribueerd
- `scan_breed` haalde brede spreiding over documenttypen op

**What was wrong / missing:**

**1. [KRITIEK] Binnen-call deduplicatie ontbreekt — 4 van 8 `zoek_uitspraken`-slots verspild**
`zoek_uitspraken` retourneerde dezelfde document_id (notulen 2025-04-10) in 4 van 8 resultaatslots (licht verschillende fragmenten, zelfde document). Doc 6115020 verscheen ook 4× in `scan_breed`. De bestaande `dedup_by_doc=True` in `_format_chunks_v3` dedupliceert alleen bij het renderen — dezelfde documenten verbruiken al meerdere `top_k`-slots in de retrieval-laag. Effect: `max_resultaten=8` levert effectief 2–3 unieke documenten in plaats van 8.

- **Root cause:** dedup zit in de formatter, niet in de retrieval-laag. Na reranking moet worden gefilterd op `document_id` (beste chunk per document bijhouden) *vóór* de `top_k`-cut. Dit is een andere klasse dan de cross-call dedup-discussie (die is de LLM's verantwoordelijkheid); dit is server-side en onoplosbaar via tool-description.

**2. Resultaatslot verspild aan "Geen stukken ontvangen"**
`zoek_uitspraken` resultaat [1] was "Ingekomen stukken: Haven en Duurzaam" met body "Geen stukken ontvangen" — een leeg agenda-item. Correct geïndexeerd, maar inhoudsloos voor retrieval. Chunk levert niets op voor de LLM en verdringt een relevanteResultaat.

- **Root cause:** geen minimale inhoudslengte-filter vóór ranking. Een chunk van <80 tekens na stripping is vrijwel altijd een lege header of artefact.

**3. Score 0.06 fragmenten in `scan_breed` zijn ruis**
Relevantiescore-bereik was 0.06–0.77 bij één query. Scores <0.15 zijn in de praktijk niet onderscheidend van willekeurige matches. Bestaande precedent: `zoek_financieel` hanteert al een drempel van 0.25 voor `table_chunks` ([mcp_server_v3.py:503](../mcp_server_v3.py#L503)).

**Scorekaart:**

| Dimensie | Score | Notitie |
|---|---|---|
| Inhoudsrelevantie | 4/5 | Beleidsdoelen en motie correct |
| Slot-efficiëntie | 2/5 | Herhaling domineert; 50% verspilling in zoek_uitspraken |
| Lege resultaten gefilterd | 2/5 | "Geen stukken ontvangen" slot verspild |
| Score-kwaliteit | 3/5 | 0.06-hits zijn ruis |

**Severity:** medium — correctheid OK, maar slot-inefficiëntie schaalt slecht: bij `max_resultaten=20` kan een veelgevraagd document effectief het hele resultatenvenster innemen.

**Triaged:** 2026-04-11 → [WS4_MCP_DISCIPLINE.md §Retrieval quality fixes](../docs/handoffs/WS4_MCP_DISCIPLINE.md) (alle drie bugs: dedup retrieval-laag, score-floor, lege-chunk-filter).

---

### 2026-04-11 zoek_financieel — Jeugdzorgkosten Rotterdam vs. GRJR-regio (scope-verwarring + OCR-artefacten)

**Query / params:**
Vraag: "Wat zijn de jeugdzorgkosten in Rotterdam?" — `zoek_financieel(onderwerp="jeugdzorg kosten Rotterdam", datum_van="2022-01-01")` + follow-up `lees_fragment` calls op GRJR-jaarstukken en begrotingsdocumenten.

**What came back:**
Tool retourneerde een bruikbare GRJR-uitvoeringsrapportage 2023 met een volledige kostentabel (jeugdhulp behandeling €73,7 mln, dagbesteding €42,8 mln, pleegzorg €31,8 mln, verblijf overig €144,8 mln, crisis/LTA/GGZ €38,0 mln, jeugdbescherming €57,2 mln, jeugdreclassering €9,1 mln, MO-WMO €11,7 mln, **totaal €409 mln**), plus een meerjaren-deelcategorie 2021–2025 (€17,6 mln → €27,5 mln, vermoedelijk ambulante jeugdhulp). Daarnaast werden politieke debatten (Barendrecht, Lansingerland, Schiedam over betaalbaarheid; Regionale Bestuursopdracht Kostenbeheersing feb-2024) correct teruggegeven.

**Wat werkte goed:**
- **Tabel-retrieval is sterk.** Het `table_chunks`-boost-pad in [mcp_server_v3.py:484-503](../mcp_server_v3.py#L484-L503) vond de gestructureerde kostentabel direct en renderde 'm als markdown — dit is precies waar de tool voor bedoeld is.
- **`datum_van`-filter werkt:** geen pre-2022-ruis (deelgemeentebegrotingen bleven weg).
- **Meerdere relevante documenten in één call** (GRJR-jaarstukken + GRJR-begroting + raadsbrief kostenbeheersing).
- **Politieke context werd correct gekoppeld** aan financiële cijfers — het narratief over regiogemeenten die klagen over betaalbaarheid kwam mee met de tabeldata.

**What was wrong / missing:**

**1. [KRITIEK] Scope-verwarring: GRJR-regio wordt gepresenteerd als "Rotterdam"**
De vraag was "jeugdzorgkosten in *Rotterdam*"; het antwoord bevat GRJR-totalen (€409 mln) die de gezamenlijke jeugdhulpuitgaven van **9 gemeenten** dekken (Rotterdam + Barendrecht, Capelle, Krimpen, Albrandswaard, Ridderkerk, Lansingerland, Maassluis, Schiedam, Vlaardingen; Rotterdam is gastgemeente). Rotterdam draagt hiervan ~75% via de inwonerbijdrage, maar dat is niet direct afleesbaar uit de tool-response.

- **Root cause (ingest):** GRJR is een *aparte rechtspersoon* onder de Wet Gemeenschappelijke Regelingen, maar zijn publicaties verschijnen op `watdoetdegemeente.rotterdam.nl` omdat Rotterdam gastgemeente is. De ingest-pipeline tagt alle documenten op dat portal als `gemeente='rotterdam'` (zie [pipeline/financial_ingestor.py:24](../pipeline/financial_ingestor.py#L24)). Er is **geen `scope` of `entity_type` dimensie** die onderscheid maakt tussen (a) Rotterdam-eigen financiën, (b) GR-financiën gepubliceerd via Rotterdam, (c) nationale/regionale aggregaten.
- **Root cause (tool-response):** `zoek_financieel` retourneert content zonder scope-metadata. De consumerende LLM krijgt geen signaal "dit is regionale data, Rotterdam-aandeel is afleidbaar via bijdrageverdeling maar niet expliciet in dit fragment" — dus het model vertelt de user dat €409 mln "de Rotterdamse jeugdzorgkosten" zijn. Stille scope-hallucination bij correct opgehaalde data.
- **Wat de user terecht opmerkte:** de Rotterdam-bijdrage *staat wel in de GRJR-tabellen* (inwonerbijdrageverdeling per deelnemer). Die tabel wordt alleen nu niet als afzonderlijke structuur geëxtraheerd; de meerderheid van de content komt terug als ongemarkeerde totalen.

**2. [KRITIEK] OCR-artefacten in tabelcontent — `"8egroting 2024"`, `"Wygng"`**
Zichtbare OCR-fouten in begrotingsdocumenten (de `B` uit "Begroting" werd `8`; "Wijziging" werd `Wygng`). Dit ondermijnt zowel weergave als semantische retrieval — een chunk met `"8egroting 2024"` matcht niet op een BM25-query voor "begroting 2024".

- **Root cause:** Docling gebruikt EasyOCR als fallback wanneer een PDF image-based is. Oudere GRJR-scans en sommige bijlagen van begrotingsstukken zijn lage-resolutie scans, niet native digitale PDFs. Er is **geen post-OCR kwaliteitssignaal** op chunk-niveau: geen CER (character error rate) meting, geen dictionary-hit-ratio tegen een Nederlands lexicon, en geen `ocr_confidence`-veld in `documents.metadata` of `chunks.metadata`. De tool weet daardoor niet dat een chunk onbetrouwbaar is, de LLM weet het ook niet, en de user ziet de artefacten pas wanneer ze de gerenderde tabel lezen.
- **Impact dubbel:** (a) indexing loss — "8egroting" is onvindbaar via zoekterm "begroting"; (b) trust loss — zichtbare rommel in het antwoord ondermijnt de waargenomen betrouwbaarheid van correcte cijfers ernaast.

**3. Gemengde topic-scope binnen één document — autoluw fonds naast jeugdhulp in `fin_begroting_2026`**
Eén retrievedocument (`fin_begroting_2026`) bevatte zowel jeugdhulp-data als een chunk over het autoluw fonds (stedelijke ontwikkeling). De autoluw-chunk is off-topic voor een jeugdzorgvraag en verbruikt een result-slot.

- **Root cause:** De chunking-strategie voor financiële documenten is waarschijnlijk te grofmazig — begrotingstabellen worden per document-sectie (of per pagina) gechunkt, niet per tabel-rij of per programma. Een chunk met label "Programma's 2026" bevat tientallen programma-regels naast elkaar; vector search op "jeugdhulp" matcht 'm omdat "jeugdhulp" erin staat, maar de chunk bevat óók autoluw fonds, participatie, etc. Verder is de `table_chunks`-relevantie-filter op 0.25 ([mcp_server_v3.py:503](../mcp_server_v3.py#L503)) permissief — laat veel borderline-matches door.
- **Fix-richting:** dit is precies het probleem dat WS2 structureel oplost met `financial_lines` (één rij per programma × jaar × bedrag_label). Zodra `vraag_begrotingsregel` bestaat, hoort `zoek_financieel` voor exacte getallen een router te zijn die doorroutet naar de structured tool — het tekst-pad wordt alleen nog gebruikt voor "waarom/toelichting"-vragen.

**4. Geen Rotterdam-aandeel direct opvraagbaar**
Bij regionale documenten is Rotterdam's aandeel een afgeleide: `rotterdam_share = grjr_total × inwonerbijdrage_pct_rotterdam`. De inwonerbijdrage staat wel in GRJR-jaarstukken maar wordt niet als structured data geëxtraheerd — dus de LLM kan het niet deterministisch berekenen.

- **Root cause:** geen dedicated extractie van `gr_member_contributions`-tabellen, en geen koppeling tussen een `financial_line` (totaal) en de bijbehorende per-deelnemer verdeling.

**5. Documentatie `budget_year` vs `datum_van` onduidelijk**
De tool-description in [mcp_server_v3.py:438-440](../mcp_server_v3.py#L438-L440) zegt "nauwkeuriger dan datum_van/datum_tot" maar geeft geen voorbeeld van *wanneer* ze divergeren. Een LLM weet niet of de correcte call `budget_year=2023`, `datum_van="2023-01-01"`, of beide is.

- **Root cause:** tool-description niet geschreven voor AI-consumptie (exacte WS4-kritiek: "use when / do NOT use when"-template ontbreekt).

**6. Duplicaat `document_ids` over meerdere tool-calls in dezelfde sessie**
`fin_begroting_2026` verscheen in zowel de initiële `zoek_financieel`-call als een vervolgcall.

- **Root cause:** token-waste, geen correctheidsprobleem — de LLM heeft de content al in context en kan er gewoon uit synthetiseren. De fix zit in de WS4 tool-description rewrite (AI-consumptie template "do NOT use when"-regel), niet in server-side infrastructuur: een `exclude_ids`-parameter lost het niet op (verschuift state management naar de LLM, hurt recall voor vervolgvragen waarbij hetzelfde document genuinely de beste bron is).

**7. `lees_fragment` ID-compatibiliteit onduidelijk**
`zoek_financieel` retourneert `document_id`-waarden, maar `lees_fragment` gedraagt zich afwijkend voor sommige ID-vormen. Al gevangen in WS4 MCP bug fixes — dit is dezelfde klasse als het `fin_jaarstukken_2019`-verkeerd-fragment-probleem uit de parkeertarieven-audit (2026-04-11).

**Scorekaart:**

| Dimensie | Score | Notitie |
|---|---|---|
| Tabel-retrieval | 4/5 | Vond GRJR-kostentabel direct, rendered als markdown |
| Datum-filter werking | 5/5 | `datum_van` filterde pre-2022 correct weg |
| Scope-duidelijkheid | 1/5 | Kritiek: regio gepresenteerd als gemeente |
| Tabelkwaliteit (OCR) | 2/5 | Zichtbare artefacten (`8egroting`, `Wygng`) |
| Topic-focus binnen doc | 3/5 | Autoluw fonds kwam mee met jeugdhulp-query |
| Rotterdam-aandeel afleidbaar | 1/5 | GRJR-totaal niet uit te splitsen zonder handmatig rekenwerk |
| Tool-documentatie (AI-consumptie) | 2/5 | `budget_year` vs `datum_van` onduidelijk |

**Severity:** high — scope-verwarring bij een financiële vraag over een specifieke gemeente is een zelfde klasse failure als de "stille hallucination bij begroting-grafiek" van 2026-04-10: de cijfers zijn technisch correct in de bron, maar de tool-response presenteert ze in een context waarin een user ze verkeerd interpreteert. Voor politieke analyse (waar een gemeenteraadslid een zuivere gemeente-vergelijking nodig heeft) is dit fundamenteel onbetrouwbaar.

**Verbeteringen — root causes → fixes:**

1. **[KRITIEK, WS2] Scope-dimensie op financial_lines + MCP response** — voeg `scope` (`gemeente` | `gemeenschappelijke_regeling` | `regio` | `nationaal`) en `entity_id` toe aan het `financial_lines`-schema. Tag GRJR-documenten bij ingest als `scope='gemeenschappelijke_regeling'`, `entity_id='grjr'`. `zoek_financieel` en `vraag_begrotingsregel` retourneren altijd `scope` + `entity` in elke match, zodat de LLM niet meer regio met gemeente kan verwarren. Zie uitgebreide beschrijving in [WS2_FINANCIAL.md §Joint-arrangement (GRJR) & scope handling](../docs/handoffs/done/WS2_FINANCIAL.md).

2. **[KRITIEK, WS2] GR-bijdrageverdeling als eerste-klas data** — GRJR-jaarstukken bevatten altijd een `inwonerbijdrage`-tabel. Nieuwe extractie-regel in `pipeline/financial_lines_extractor.py`: voor documenten met `scope='gemeenschappelijke_regeling'`, ook de deelnemersverdeling extraheren naar een `gr_member_contributions`-tabel (`entity_id`, `member_gemeente`, `jaar`, `bijdrage_eur`, `bijdrage_pct`). `vraag_begrotingsregel(gemeente='rotterdam', ..., include_gr_shares=True)` kan dan voor regionale totalen automatisch Rotterdam's afgeleide aandeel meeberekenen. Zie [WS2_FINANCIAL.md §Joint-arrangement (GRJR) & scope handling](../docs/handoffs/done/WS2_FINANCIAL.md).

3. **[WS2] OCR-kwaliteitsmetadata per chunk** — bij ingest van financiële documenten, bereken (a) `ocr_backend` (`native_pdf` | `easyocr` | `tesseract`), (b) `ocr_dict_hit_ratio` (fraction van tokens die matchen tegen een Nederlands lexicon — goedkoop, geen ML nodig), (c) `ocr_confidence` als Docling het rapporteert. Opslaan in `chunks.metadata`. Twee downstream gevolgen: filter low-quality chunks uit de default retrieval (drempel `dict_hit_ratio >= 0.85`) of annoteer ze expliciet in de response (`quality_warning: "ocr_artefacten_waarschijnlijk"`). Zie [WS2_FINANCIAL.md §Joint-arrangement (GRJR) & scope handling](../docs/handoffs/done/WS2_FINANCIAL.md).

4. **[WS2] Finer-grained table chunking bij financiële docs** — al impliciet gedekt door `financial_lines` structured extractie (één rij per cel); na go-live hoort `zoek_financieel` voor exacte getallen een router te zijn die doorroutet naar `vraag_begrotingsregel`. Het tekst-pad verdwijnt niet maar is alleen nog voor narratieve vragen. Reeds in [WS2_FINANCIAL.md §MCP tools](../docs/handoffs/done/WS2_FINANCIAL.md) opgenomen.

5. **[WS4] `budget_year` vs `datum_van` documentatie herschrijven naar AI-consumptie template** — opgenomen in de tool-description-rewrite van de bestaande 13 tools. Concreet voorbeeld dat de divergentie illustreert: "Begroting 2025 wordt ingediend in oktober 2024 (publicatiedatum) maar beschrijft fiscaal jaar 2025 (budget_year). Gebruik `budget_year=2025` wanneer je vraagt 'wat is de begrotingsruimte voor 2025' en `datum_van='2024-10-01'` wanneer je vraagt 'welke begrotingsdocumenten werden gepubliceerd in oktober 2024'." Ingewerkt in [WS4_MCP_DISCIPLINE.md §Tool API improvements](../docs/handoffs/WS4_MCP_DISCIPLINE.md).

6. **[WS4] `exclude_ids` parameter op alle retrieval-tools** — goedkope server-side dedup voor multi-call sessies. LLM stuurt de document_ids die al geconsumeerd zijn mee; de tool filtert ze uit. Ingewerkt in [WS4_MCP_DISCIPLINE.md §Tool API improvements](../docs/handoffs/WS4_MCP_DISCIPLINE.md).

**Triaged:** 2026-04-11 → [WS2_FINANCIAL.md §Joint-arrangement (GRJR) & scope handling](../docs/handoffs/done/WS2_FINANCIAL.md) (primary, items 1–4) + [WS4_MCP_DISCIPLINE.md §Tool API improvements](../docs/handoffs/WS4_MCP_DISCIPLINE.md) (item 5: `budget_year` docs + scope metadata passthrough). Item 6 (duplicaat doc_ids): token-waste, geen infra fix — adres via tool-description "do NOT use when"-clausule in WS4. Issue 7 (`lees_fragment` ID-compatibiliteit) reeds gedekt door bestaande WS4 MCP bug fixes.

---

### 2026-04-11 zoek_raadshistorie / lees_fragment — Parkeertarieven Rotterdam (Heemraadssingel) 2000–heden

**Query / params:**
Vraag: ontwikkeling parkeertarieven in Rotterdam, specifiek rond Heemraadssingel, van 2000 tot heden. 12 sequentiële tool calls (zie tabel hieronder).

| # | Tool | Query | Resultaat |
|---|---|---|---|
| 1 | `zoek_raadshistorie` | parkeertarieven Rotterdam ontwikkeling Heemraadssingel | ❌ Geen Heemraadssingel-hits, generieke parkeerdocs |
| 2 | `zoek_raadshistorie` | parkeertarieven betaald parkeren tarief verhoging Rotterdam | ⚠️ Politieke debatfragmenten, geen tarieftabellen |
| 3 | `zoek_raadshistorie` | Heemraadssingel parkeren betaald parkeerzone uitbreiding | ❌ Geen straatniveau-hit, alleen zone/sectorinformatie |
| 4 | `zoek_raadshistorie` | parkeren binnen de ring uitbreiding tarieven 2022 2023 2024 | ✅ Coalitieakkoord-maatregelen + financiële tabel |
| 5 | `zoek_raadshistorie` | parkeertarief euro per uur straatparkeren bewonersvergunning bedrag | ✅ 2024-tarieftabel (doc 6114600) + 2008 €9/maand |
| 6 | `zoek_raadshistorie` | parkeerbelastingtarieven verordening tarieventabel zone A B C | ⚠️ Verordeningreferenties, geen tariefwaarden |
| 7 | `zoek_raadshistorie` | parkeertarief 2015 2016 2017 2018 middengebied | ❌ Geen resultaten voor mid-period tarieven |
| 8 | `lees_fragment` | doc 6114600 | ✅ Volledige 2024-tarieftabel bevestigd |
| 9 | `lees_fragment` | doc 246823 | ❌ Document bevatte projectdata, geen parkeertarieven — verkeerd gelabeld in zoekresultaten |
| 10 | `lees_fragment` | doc 239516 | ⚠️ Alleen legenda (€1.50 / €2.30 / €3.00), geen zone-attributie |
| 11 | `lees_fragment` | fin_jaarstukken_2019 | ❌ Verkeerd fragment teruggegeven — financieel overzicht i.p.v. parkeerparagraaf |
| 12 | `zoek_raadshistorie` | Middelland betaald parkeren 2019 venstertijden | ✅ Bevestigde 1 juli 2019-uitbreiding expliciet |

**What came back:**
Bruikbare beleidsbeslissingen en politieke debatten voor 2019–2024, maar grote gaten in tarieftabellen tussen 2001–2018. Eén bevestigde tarieftabel (2024, doc 6114600). Eén false positive (doc 246823) waarbij de zoeksnippet niet matchte met de werkelijke documentinhoud.

**Wat werkte goed:**
- **Semantic search voor beleidsbeslissingen** is sterk: raadsbesluiten, coalitieakkoord en venstertijden werden accuraat en citeerbaar opgehaald.
- **Doc 6114600** (2024-tarieftabel) werd betrouwbaar gevonden bij meerdere queryformuleringen.
- **Positief — straat→buurt-afleiding:** De LLM kon correct de buurt afleiden uit de straatnaam (Heemraadssingel → Middelland). De semantische laag werkt; alleen de **indexering** mist het mapping-verband (zie verbeterpunt 1).

**What was wrong / missing:**

**1. Geen straat-/sectorindex**
"Heemraadssingel" gaf nul relevante hits. Documenten worden niet getagd op straatnaam of gekoppeld aan parkeer-sectorcodes. Een gebruiker die naar een specifieke locatie vraagt wordt doorgeleid naar stedelijk beleid zonder bridge naar lokale context.

**2. Tariefdata schaars voor 2001–2018**
Sterk in beleidsdiscussies, zwak in feitelijke tarieftabellen. Annexen van begrotingen/jaarstukken zijn waarschijnlijk niet geïndexeerd of zitten in scanned PDFs. De MCP kan niet onderscheiden tussen "document bestaat niet" en "document bestaat maar werd niet opgehaald".

**3. `lees_fragment` retourneerde verkeerd fragment bij `fin_jaarstukken_2019`**
`zoek_raadshistorie` vond het juiste fragment (Middelland venstertijden), maar `lees_fragment` op hetzelfde `document_id` gaf financiële samenvattingstabellen terug. Het fragment-ranking binnen een document is niet gepind aan de query die hem vond.

**4. False positive op doc 246823 (HOOGSTE PRIORITEIT)**
Zoeksnippet toonde "Centrum €3.50 / Buiten centrum €2.00", maar bij `lees_fragment` bleek het document een GroenLinks-kaderbrief over stedelijke ontwikkeling — geen parkeercontent. **Dit duidt op een chunk→document_id-mismatch.** De snippet kwam mogelijk uit een ander document maar werd toegeschreven aan 246823. Dit is het gevaarlijkste failure-mode: een betrouwbaar ogend citaat dat niet bestaat in de bronpaper.

**5. Geen coverage-/confidence-signaal**
Bij "parkeerbelasting 2005" weet de consumerende LLM niet of het ontbreken van resultaten betekent (a) geen documenten bestaan, (b) documenten bestaan maar zijn niet geïndexeerd, of (c) de query matchte niet. Dwingt tot stille hallucination om gaten op te vullen.

**Scorekaart:**

| Dimensie | Score | Notitie |
|---|---|---|
| Beleids-/besluitretrieval | 8/10 | Sterk voor benoemde besluiten en raadsbesluiten |
| Tariefdata-retrieval | 3/10 | Schaars voor 2022; alleen 2024-tabel expliciet gevonden |
| Straat-/locatiespecificiteit | 2/10 | Geen straatniveau-indexering |
| Fragment-accuratesse | 6/10 | Eén false positive, één verkeerd fragment |
| Temporele coverage-signal | 0/10 | Geen coverage-metadata exposed |
| Hallucinatierisico | Hoog | Schaarse historische data forceert schattingen |

**Severity:** high — combinatie van false-positive chunk attribution + ontbrekende coverage signal + sparse historical tariff data maakt dit type vraag fundamenteel onbetrouwbaar voor citeerbare politieke analyse.

**Verbeteringen voor v0.2:**

1. **[KRITIEK] Audit chunk→document_id-attributie.** Doc 246823-incident suggereert dat chunks mogelijk verkeerd gemapt zijn naar documenten. Schrijf een diagnostiek script dat per chunk verifieert: `chunk.document_id == document.id` waar de chunk fysiek vandaan kwam in de ingest pipeline. Hoogste prioriteit — dit ondermijnt elk citaat.

2. **Locatie-indexering: drie-laagse aanpak (vervangt eerste statische-JSON-suggestie).** *Verfijnd 2026-04-11 na review.*
   - **2a. [Quick win] Layer 1 enrichment uitbreiden — match `domain_gazetteer.json` ook tegen chunk-tekst, niet alleen parent-documenttitel.** Huidige staat ([PLAN_GI_MERGED_STATUS.md:14](../docs/architecture/PLAN_GI_MERGED_STATUS.md#L14)): `key_entities` is gevuld voor 28% van chunks via gazetteer-match op de doc-titel. Een chunk die "Heemraadssingel" noemt in een doc getiteld "Voortgangsrapportage parkeren 2024" krijgt geen tag → onvindbaar via Qdrant payload-filter. Tweede pass van `scripts/enrich_and_extract.py` over chunk-bodies. Geen NER, geen LLM, lost direct het Heemraadssingel-failure type op voor de 2.217 entiteiten die al in de gazetteer staan.
   - **2b. [Structureel] Layer 2 Flair NER (`ner-dutch-large`) op chunk-tekst** zoals al gepland in [PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md](../docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md). Vangt straat-/landmark-/buurtnamen die niet in de gazetteer zitten. Coverage doel 28% → ~75% (combined met 2a).
   - **2c. [Strategisch] BAG-hiërarchie als `LOCATED_IN` KG-edges.** Import van [PDOK BAG](https://www.pdok.nl/introductie/-/article/basisregistratie-adressen-en-gebouwen-ba-1) + [CBS Wijk- en Buurtkaart](https://www.cbs.nl/nl-nl/dossier/nederland-regionaal/geografische-data/wijk-en-buurtkaart-2024) → ~5.000 straten + ~80 buurten + 14 gebieden + 1 gemeente voor Rotterdam. Maakt cross-level queries mogelijk via dezelfde graph-traversal als andere KG-relaties. **Multi-tenant design constraint locked-in:** Location nodes gekeyed op BAG 16-digit `openbare_ruimte` ID (NIET straatnaam — collisies over gemeenten heen), verplichte `gemeente` attribuut, `LOCATED_IN` edge met `level`-attribuut zodat Amsterdam stadsdelen / Utrecht wijken / Rotterdam gebieden allemaal in hetzelfde schema passen. Nu vastleggen kost niets extra; v0.2.1 multi-portal krijgt locatielaag essentieel gratis.
   - **NIET doen:** statische JSON `straat → sector → documenten` als primair retrieval-mechanisme. Dit was mijn eerste suggestie en is structureel verkeerd: lost alleen query-rewriting op, niet het indexerings-probleem. Een chunk die Heemraadssingel noemt blijft onvindbaar tenzij we 'm taggen.
   - **Ingewerkt in:** [WS1_GRAPHRAG.md §Phase A](../docs/handoffs/WS1_GRAPHRAG.md), [V0_2_BEAT_MAAT_PLAN.md §3.1](../docs/architecture/V0_2_BEAT_MAAT_PLAN.md), [WS5b_MULTI_PORTAL.md](../docs/handoffs/WS5b_MULTI_PORTAL.md) (BAG-skeleton import per nieuwe gemeente).

3. **Dedicated tariff/annex-index uit financiële documenten.** Begrotingen en jaarstukken hebben consistente jaarlijkse tarieventabellen. Aparte embedding run over annexen + table_json chunks (al beschikbaar volgens memory `project_financial_docs`). Mogelijk uitbreiden van bestaande `zoek_financieel` tool met tariff-aware ranking.

4. **`lees_fragment` accepteert optionele `query` parameter.** Re-rank fragmenten binnen het document op queryrelevantie i.p.v. default fragment 1–5. Voorkomt dat de juiste paragraaf "verloren gaat" tussen ophalen en lezen.

5. **Coverage-metadata in tool responses.** Voeg `corpus_coverage: {period, document_count, indexed_types}` toe aan zoekresponses. Stelt de LLM in staat om expliciet "geen data voor 2005–2010 in dit corpus" te zeggen i.p.v. te schatten.

6. **Snippet provenance verification.** Voor elke search hit: verifieer dat de getoonde snippet daadwerkelijk in het document met dat ID staat. Een goedkope sanity-check die false positives zoals doc 246823 vroeg vangt.

---

### 2026-04-10 zoek_financieel — Hallucinated datapunten bij eerste begroting-grafiek (2018–2024)

**Query / params:**
Vraag: overzicht van Rotterdamse begroting/realisatie per cluster 2018–2024, gevisualiseerd als grafiek.

**What came back:**
Eerste poging produceerde een grafiek met geschatte/hallucinated waarden. Voorbeeld: Zorg 2018 werd geschat op ~€725k i.p.v. de werkelijke realisatie €791.744k (VGZ €607.965 + MO €183.779 uit eindejaarsbrief 2019). Stedelijke ontwikkeling was onvolledig opgehaald.

Na correctie (tweede iteratie met verificatie via primaire bronnen in NeoDemos) kwamen correcte cijfers:

| Cluster | 2018 (€k) | 2024 (€k) |
|---|---|---|
| Zorg totaal | 791.744 | 1.431.228 |
| Stedelijke ontw. | 356.686 | 565.648 |
| Beheer van de stad | 342.442 | 526.656 |
| Veilig | 138.499 | 217.379 |
| Werk en inkomen | 737.325 | 899.826 |
| Overhead | 463.347 | 465.953 |

**What was wrong / missing:**
- Eerste poging vulde ontbrekende waarden aan met schattingen zonder dit expliciet te melden — stille hallucination.
- Clusters worden in NeoDemos-documenten anders ingedeeld per jaar (bijv. "Stedelijke ontwikkeling" vs. "SIO-programma" vs. "Bestaande stad") → aggregatie vereist expliciete mappinglogica.
- **Positief:** De visualisatiestijl en het idee om verwante begrotingsclusters samen te voegen werden gewaardeerd.
- **Standaard beschikbaar:** Rotterdam (en alle gemeenten) rapporteren conform de **IV3-indeling / taakvelden**. Zie [findo.nl/content/vraagbaak-iv3](https://findo.nl/content/vraagbaak-iv3) en [findo.nl/content/taakvelden-gemeenten](https://findo.nl/content/taakvelden-gemeenten). Deze standaard kan als canonieke aggregatielaag dienen zodat clusters consistent zijn over jaren heen.

**Severity:** high — stille hallucination bij financiële cijfers is misleidend voor politieke analyse. Fix: (1) nooit schatten zonder expliciete disclaimer, (2) IV3-taakvelden implementeren als gestandaardiseerde aggregatielaag in de financiële pipeline.

---

### 2026-04-10 zoek_moties / zoek_raadshistorie — Initiatiefvoorstel Engberts & Vogelaar (leegstand) ontbreekt in resultaten

**Query / params:**
Vraag: "Welke maatregelen rond leegstand zijn de afgelopen vier jaar verworpen, en hoe stemde de VVD?"
Meerdere tool calls: `zoek_moties` (leegstand), `zoek_raadshistorie`, `lees_fragment` voor relevante moties.

**What came back:**
Overzicht van 4 verworpen moties/amendementen (SP, GroenLinks, BIJ1, etc.) inclusief VVD-positie. Geen vermelding van het initiatiefvoorstel van Engberts & Vogelaar over leegstand.

**What was wrong / missing:**
Het **initiatiefvoorstel van Engberts & Vogelaar** over leegstand ontbreekt volledig in de resultaten. Dit is een raadsvoorstel van (vermoedelijk) Leefbaar Rotterdam-raadsleden — een ander documenttype dan moties/amendementen. Mogelijke oorzaken:
- Initiatiefvoorstellen worden niet of onvolledig geïndexeerd
- Documenttype `initiatiefvoorstel` zit niet in de zoekvectorruimte van `zoek_moties`
- Partijfilter of metadata-tag voor indieners van dit voorstel mist
- Voorstel is mogelijk opgenomen als agendapunt zonder aparte chunk

**Severity:** high — een initiatiefvoorstel is een zwaarder politiek instrument dan een motie; als dit type document niet terugkomt bij thematische searches is de volledigheid van het platform fundamenteel onbetrouwbaar voor politieke analyse.

---

### 2026-04-10 Algemeen — Trage responsetijd bij leegstand-overzichtsvraag

**Query / params:**
Dezelfde vraag als hierboven (meerdere tool calls in sequentie: zoeken → ophalen fragmenten → synthetiseren).

**What came back:**
Correct antwoord na lange wachttijd.

**What was wrong / missing:**
Antwoord duurde significant lang. Waarschijnlijk oorzaak: sequentiële tool calls (zoek → lees per motie) in plaats van parallelle ophaling. Pipeline voert momenteel geen fan-out uit bij multi-document retrieval.

**Severity:** medium — correctheid OK, maar UX-impact is groot bij overzichtsvragen die meerdere documenten vereisen. Fix: parallelle `lees_fragment` calls of een dedicated `zoek_breed`-aggregator met ingebouwde dedup.

