# Agentic MedTech Research System
## Project Plan and Architecture

## 1. Project Vision

Build a **multi-source research copilot for MedTech and FDA research** that allows researchers to ask natural-language questions and receive a synthesized, cited answer across structured and unstructured data sources.

The system should not be thought of as only a text-to-SQL application. Instead, **text-to-SQL is one component of a broader research agent**.

Example user question:

> Tell me the FDA history of Foqus Technologies along with its private equity investments, funding details, and relevant public filing information.

The system should interpret this as:

1. Identify and resolve the correct company entity.
2. Query the FDA database for approval history.
3. Query a PitchBook-derived database for investment and funding history.
4. Search trusted public sources for corporate history, filings, acquisitions, and related information.
5. Reconcile the evidence.
6. Produce a concise, cited research brief.

---

## 2. Core Product Concept

### High-Level Flow

```text
Researcher
    |
    v
Natural-Language Question
    |
    v
Agent / Query Planner
    |
    +-------------------+---------------------+
    |                   |                     |
    v                   v                     v
FDA SQL Tool      PitchBook SQL Tool     Web Research Tool
    |                   |                     |
    +-------------------+---------------------+
                        |
                        v
                Entity Resolution
                        |
                        v
                 Evidence / Fact Layer
                        |
                        v
                   LLM Synthesis
                        |
                        v
               Cited Research Response
```

The main value is not simply generating SQL. The system must solve five related problems:

1. Natural-language to structured-data querying
2. Entity resolution
3. Query planning and source routing
4. Evidence normalization and reconciliation
5. Final answer synthesis with citations

---

# 3. Component Architecture

## 3.1 Researcher Front End

A lean application where researchers can:

- Ask questions in natural language
- View the synthesized answer
- Inspect FDA records used
- Inspect PitchBook records used
- View public sources and citations
- Optionally view generated SQL
- Export or copy research findings

### Recommended V1

Use **Streamlit** for speed.

Possible later migration:

- React / Next.js frontend
- FastAPI backend

Example layout:

```text
------------------------------------------------------------
 MedTech Research Copilot

 Ask about a company, FDA approvals, investments, etc.

 [ Tell me the history of Foqus Technologies              ]
                                                       Ask
------------------------------------------------------------

 Foqus Technologies

 Overview
 Founded:
 Headquarters:
 Current status:

 FDA History
 ----------------------------------------------------------
 Date       Submission       Device        Product Code

 Investment History
 ----------------------------------------------------------
 Date       Round            Amount        Investors

 Corporate Events
 ----------------------------------------------------------
 Date       Event

 Sources
 FDA | PitchBook | SEC | Company Website
------------------------------------------------------------
```

---

# 4. Text-to-SQL Layer

The text-to-SQL component converts natural-language questions into safe SQL queries.

Example question:

> Show Foqus Technologies' FDA approvals since 2015.

Potential generated SQL:

```sql
SELECT
    device_name,
    decision_date,
    product_code,
    regnumber
FROM devices
WHERE company_id = 1283
  AND decision_date >= '2015-01-01'
ORDER BY decision_date;
```

## Required Inputs for Reliable Text-to-SQL

The LLM should not receive only raw table schemas.

It should also receive:

- Table descriptions
- Column definitions
- Relationships between tables
- Business definitions
- Example questions and SQL
- Rules for interpreting FDA-specific concepts

Example semantic definitions:

```text
DECISION_DATE
Date on which the FDA made its decision.

REGNUMBER
FDA submission identifier.

First FDA approval
Minimum DECISION_DATE associated with the canonical company.

Company
Must be resolved through company_id rather than matching raw
APPLICANT values directly.
```

This acts as a lightweight **semantic layer**.

---

# 5. Entity Resolution Layer

Entity resolution is likely to be one of the most important components.

The same company may appear differently across systems.

Example:

```text
FDA:
FOQUS TECHNOLOGIES LTD

PitchBook:
Foqus Technologies

Public filing:
Foqus Technologies Inc.
```

Before running downstream queries, the system should map these variants to a canonical entity.

## Suggested Master Company Table

```text
companies
---------
company_id
canonical_company_name
website
pitchbook_id
sec_cik
parent_company_id
acquired_by_company_id
valid_from
valid_to
```

## Suggested Alias Table

```text
company_aliases
---------------
alias_id
company_id
alias
alias_type
source
confidence
```

Example:

```text
User asks:
"Foqus Technologies"

        |
        v

Entity Resolver

        |
        v

Canonical company_id = 1837

FDA aliases:
- FOQUS TECHNOLOGIES LTD
- FOQUS TECHNOLOGIES

PitchBook ID:
PB-XXXXXX

SEC CIK:
XXXXXXX
```

All downstream tools should query using the resolved entity whenever possible.

---

# 6. Agent / Query Planner

The agent determines what sources are required for a question.

### Simple FDA Question

> How many AI-enabled devices were approved annually since 2015?

Planner:

```text
FDA database only
    |
    v
Generate SQL
    |
    v
Execute
    |
    v
Return analysis
```

### Cross-Source Company Question

> Tell me the history of Foqus Technologies.

Planner:

```text
1. Resolve Foqus Technologies
2. Query FDA approval history
3. Query PitchBook financing history
4. Search trusted public sources
5. Normalize evidence
6. Resolve conflicts
7. Build chronological timeline
8. Generate cited summary
```

## Recommended Initial Approach

Do not begin with a complicated multi-agent architecture.

Start with **one orchestrator and a few deterministic tools**.

Example conceptual Python interface:

```python
tools = [
    resolve_company,
    query_fda_database,
    query_pitchbook_database,
    search_public_sources
]
```

The orchestrator determines which tools are needed for each question.

A framework such as LangGraph can be introduced later if branching, retries, checkpoints, or complex workflows become necessary.

---

# 7. FDA SQL Tool

Responsibilities:

1. Receive structured query intent
2. Retrieve relevant schema context
3. Generate SQL
4. Validate SQL
5. Execute against a read-only database
6. Return structured results

Example result:

```json
{
  "source": "FDA",
  "company_id": 1837,
  "records": [
    {
      "regnumber": "KXXXXXX",
      "decision_date": "2021-06-04",
      "device_name": "Example Device",
      "product_code": "ABC"
    }
  ]
}
```

---

# 8. PitchBook SQL Tool

PitchBook information should ideally be transformed into a queryable internal database rather than sending raw files to the LLM repeatedly.

Potential tables:

```text
pitchbook_companies
pitchbook_financing_rounds
pitchbook_investors
pitchbook_deals
pitchbook_acquisitions
```

Example question:

> How much funding did Foqus Technologies raise before its first FDA approval?

The orchestrator could:

1. Query FDA database for first approval date
2. Query PitchBook transactions before that date
3. Aggregate capital raised
4. Generate the final answer

This enables cross-dataset research questions that are more valuable than ordinary text-to-SQL.

---

# 9. Public Web / Filing Research Tool

The system can use public research for information not contained in FDA or PitchBook.

Priority sources could include:

1. SEC filings
2. Government corporate registries
3. Official company websites
4. Official press releases
5. Acquisition announcements
6. Reputable business databases or publications

The web research tool should return structured evidence rather than only prose.

Example:

```json
{
  "event_type": "ACQUISITION",
  "company": "Example Company",
  "date": "2024-03-17",
  "acquirer": "XYZ Corp",
  "source_url": "...",
  "source_type": "SEC Filing"
}
```

---

# 10. Evidence / Fact Layer

Do not send raw SQL output and large amounts of webpage text directly to the final answer model.

Normalize evidence first.

Example structure:

```json
{
  "company": "Foqus Technologies",
  "facts": [
    {
      "type": "FDA_CLEARANCE",
      "date": "2021-06-04",
      "description": "Received 510(k) clearance for Example Device.",
      "source": "FDA",
      "source_id": "KXXXXXX"
    },
    {
      "type": "FUNDING",
      "date": "2022",
      "amount": "$12M",
      "investors": [
        "Investor A",
        "Investor B"
      ],
      "source": "PitchBook"
    },
    {
      "type": "ACQUISITION",
      "date": "2024-03-17",
      "acquirer": "XYZ Corp",
      "source": "SEC Filing"
    }
  ]
}
```

Core design principle:

> **The evidence layer determines facts. The LLM writes the narrative.**

This reduces hallucinations and makes the system easier to audit.

---

# 11. LLM Synthesis Layer

The final LLM receives normalized facts and produces a concise research report.

Potential answer structure:

```text
Foqus Technologies

Overview
...

FDA History
...

Funding and Investment History
...

Corporate History
...

Timeline
...

Sources
...
```

The model should:

- Never invent missing dates
- Clearly distinguish verified facts from uncertain information
- Prefer primary sources
- Surface conflicting information
- Preserve citations
- Avoid repeating the same information across sections

---

# 12. SQL Safety Architecture

Generated SQL should never execute unrestricted.

Recommended flow:

```text
LLM generates SQL
        |
        v
SQL Parser / Validator
        |
        +--> SELECT only?
        +--> Allowed tables?
        +--> Allowed columns?
        +--> Row limit?
        +--> No INSERT / UPDATE / DELETE / DROP?
        |
        v
Query Validation / EXPLAIN
        |
        v
Read-Only Database User
        |
        v
Execute Query
```

For an early prototype, minimum safeguards should include:

- Read-only database credentials
- SELECT-only queries
- Query timeout
- Row limits
- Table allowlist
- Logging of generated SQL

---

# 13. Recommended Technology Stack

## V1

### Front End
- Streamlit

### Backend
- Python

### API Layer
- FastAPI if needed

### Database
- PostgreSQL for production-style development
- DuckDB for rapid local prototyping

### LLM
- OpenAI, Anthropic, or equivalent model with tool-calling support

### Text-to-SQL Context
- Schema metadata
- Column descriptions
- Few-shot SQL examples
- FDA-specific semantic definitions

### Vector Store
Optional initially.

Possible choices:

- pgvector
- Chroma

Useful for retrieving:

- schema descriptions
- example queries
- research documents
- company aliases

### Agent Orchestration
Start with plain Python.

Move to LangGraph only when workflow complexity justifies it.

### Observability

Store:

```text
user_question
resolved_entities
selected_tools
generated_sql
sql_results
web_sources
normalized_facts
final_answer
user_feedback
```

These logs will be extremely useful for evaluating and debugging the system.

---

# 14. Development Roadmap

## Phase 1 — FDA Copilot

Goal:

Build a reliable natural-language interface to the FDA dataset.

Architecture:

```text
Natural Language Question
        |
        v
Schema / Semantic Retrieval
        |
        v
Text-to-SQL
        |
        v
SQL Validation
        |
        v
FDA Database
        |
        v
Answer / Table / Chart
```

Example questions:

- How many AI-enabled devices were approved annually since 2015?
- Which companies received their first 510(k) clearance in 2023?
- Show all approvals for HeartFlow.
- Which product codes experienced the fastest approval growth?
- Compare first FDA approval timing for dental AI versus radiology AI companies.

### Success Criteria

- SQL correctness
- Correct company mapping
- Correct filtering
- Reasonable answer latency
- Ability to inspect generated SQL
- Minimal hallucination

---

## Phase 2 — FDA + PitchBook Research

Add investment and funding data.

Architecture:

```text
                 +--> FDA SQL
Question -> Agent
                 +--> PitchBook SQL
                         |
                         v
                     Synthesis
```

Example questions:

- Which companies raised Series B funding before their first FDA approval?
- What was the median capital raised before first clearance?
- Which investors most frequently backed companies before FDA clearance?
- How does time-to-first-FDA-clearance vary by funding stage?
- Which companies raised less than $50M before their first approval?

This phase creates significantly more research value than a standard SQL chatbot.

---

# 15. Phase 3 — Full Research Agent

Add public research capabilities.

Sources:

```text
FDA
PitchBook
SEC
Corporate registries
Company websites
Press releases
Other approved public sources
```

Example workflow:

```text
User:
"Tell me the full history of Company X."

        |
        v

Resolve Company X

        |
        +--> FDA history
        |
        +--> PitchBook financing
        |
        +--> SEC filings
        |
        +--> Corporate registry
        |
        +--> Company / acquisition sources

        |
        v

Normalize facts

        |
        v

Resolve conflicts

        |
        v

Produce chronological research brief
```

---

# 16. Example Advanced Research Query

A strong end-state query could be:

> Find AI medical-device companies founded since 2015 that raised less than $50M before their first FDA clearance, then show their subsequent funding trajectory and acquisition history.

This question requires:

- Natural-language understanding
- SQL generation
- FDA querying
- PitchBook querying
- Entity resolution
- Cross-database matching
- Temporal reasoning
- Investment aggregation
- Public web research
- M&A research
- Evidence reconciliation
- Final synthesis

This illustrates why the project should be positioned as a **research intelligence platform**, rather than only a text-to-SQL application.

---

# 17. Key Risks

## Entity Matching

The same company may appear under multiple names.

Mitigation:

- Canonical company table
- Alias table
- Manual mappings for known entities
- Confidence scores
- Human review for ambiguous matches

## SQL Hallucination

Generated SQL may be syntactically valid but semantically wrong.

Mitigation:

- Semantic metadata
- Few-shot examples
- SQL validation
- Query logging
- Evaluation dataset

## Source Conflicts

PitchBook, FDA, SEC, and company websites may report different dates.

Mitigation:

- Source hierarchy
- Keep source metadata for every fact
- Explicit conflict detection
- Do not automatically overwrite conflicting facts

## Hallucinated Corporate History

Mitigation:

- Require citations
- Synthesize only from normalized facts
- Prefer primary sources
- Mark unavailable facts as unavailable rather than guessing

## Excessive Complexity

Mitigation:

Do not build the complete agent first.

Recommended order:

```text
FDA Text-to-SQL
      |
      v
Reliable FDA Copilot
      |
      v
PitchBook Integration
      |
      v
Entity Resolution
      |
      v
Cross-Database Research
      |
      v
Web Research
      |
      v
Full Research Agent
```

---

# 18. Suggested Project Positioning

Instead of:

> Text-to-SQL application for FDA data

Position the project as:

> **Agentic MedTech Research System**

or:

> **MedTech Research Copilot**

Possible project description:

> An AI research copilot that combines natural-language querying, text-to-SQL, entity resolution, structured FDA and investment datasets, and public-source research to answer complex MedTech market and company intelligence questions with traceable evidence.

---

# 19. MVP Recommendation

The first usable MVP should contain only:

```text
FDA PostgreSQL / DuckDB Database

        +

Company / Alias Mapping

        +

Natural Language -> SQL

        +

SQL Validation

        +

LLM Explanation

        +

Streamlit Interface
```

Do not add PitchBook or web search until the FDA SQL layer is reliable.

Once the MVP works, add:

1. PitchBook database
2. Entity resolver
3. Query planner
4. Public research
5. Evidence reconciliation
6. Full multi-source synthesis

---

# 20. Long-Term Vision

The final system should behave less like a chatbot and more like an analyst.

```text
Research Question
        |
        v
Understand Question
        |
        v
Identify Required Sources
        |
        v
Resolve Companies / Entities
        |
        v
Query Structured Databases
        |
        v
Research Public Evidence
        |
        v
Reconcile Facts
        |
        v
Perform Analysis
        |
        v
Generate Cited Research Brief
```

The differentiator is therefore not merely natural-language SQL generation.

It is the ability to combine **proprietary structured datasets with public evidence and analytical reasoning** to answer research questions that would otherwise require substantial manual work.
