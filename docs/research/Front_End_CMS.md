# NeoDemos Web Development Blueprint
## Complete Research & Migration Guide — April 13, 2026

> **Purpose:** This document compiles all research, recommendations, and implementation
> instructions from a comprehensive planning session for NeoDemos.nl — a Dutch civic
> intelligence platform for Rotterdam city council data. Feed this to Claude Code as
> your master specification for frontend development, CMS setup, and deployment.

---

## Table of Contents

1. [Context & Current State](#1-context--current-state)
2. [Part 1: Building Production Software with Claude Code](#2-part-1-building-production-software-with-claude-code)
3. [Part 2: Mac-Based Design Tools — What Actually Works](#3-part-2-mac-based-design-tools)
4. [Part 3: Payload CMS — The Clear Winner for NeoDemos](#4-part-3-payload-cms)
5. [Part 4: Docker Compose Deployment on Hetzner CCX33](#5-part-4-docker-compose-deployment)
6. [Part 5: NeoDemos-Specific Integration Recommendations](#6-part-5-neodemos-specific-integrations)
7. [Part 6: Fixing the MCP Authentication Error](#7-part-6-mcp-authentication-fix)
8. [Part 7: Payload CMS v3 Migration — Step-by-Step](#8-part-7-payload-cms-migration)
9. [Part 8: Mac Desktop App Analysis — Full Comparison](#9-part-8-mac-desktop-app-analysis)
10. [Part 9: The Definitive Daily Workflow](#10-part-9-definitive-daily-workflow)
11. [Part 10: MCP Diagnostic Feedback](#11-part-10-mcp-diagnostic-feedback)
12. [Appendix A: Cost Summary](#appendix-a-cost-summary)
13. [Appendix B: Claude Code Instructions for This Project](#appendix-b-claude-code-instructions)

---

## 1. Context & Current State

**Founder:** Dennis — Rotterdam city council member (PRO Rotterdam), banker by background, limited coding experience. Uses Claude Code and Claude Cowork as primary development tools.

**Server:** Hetzner CCX33 (8 vCPU, 32GB RAM, Ubuntu)

**Current stack:**
- FastAPI backend (Python) with MCP server at `https://mcp.neodemos.nl/mcp`
- PostgreSQL 16 + pgvector + Qdrant for RAG
- ~90,000 documents, ~1.6M text chunks (Rotterdam council data, 2018–2026 complete)
- Caddy reverse proxy, Authentik auth server
- No proper frontend framework deployed yet (Next.js planned)
- No CI/CD pipeline

**Core problem:** The frontend is fragile — changes break things, no template management, inconsistent styling, no admin panel for content management.

**12-month win condition:** Press and political recognition as the civic AI standard in the Netherlands — not revenue or enterprise contracts.

---

## 2. Part 1: Building Production Software with Claude Code

### The Fragile AI Code Problem

Research from CodeRabbit (2025–2026) found that AI-co-authored pull requests have 1.7× more issues than human-authored ones — 1.75× more correctness issues, 1.64× more maintainability issues, and 1.57× more security issues. Common failure modes include hallucinated APIs, happy-path-only logic, business logic blindness, and over-engineering.

**The single most effective mitigation is test-first development:** define expected behavior in test cases before prompting Claude Code for implementation.

### CLAUDE.md — The Highest-Leverage Single Action

Create this file at the project root. It is Claude Code's persistent memory.

```markdown
# Project Overview
NeoDemos: Dutch civic intelligence platform.
Next.js 15 frontend + FastAPI backend, deployed on Hetzner CCX33.

# Code Style
- TypeScript strict mode — no `any` types
- React: functional components, prefer server components
- Use shadcn/ui from components/ui/ — NEVER install alternative UI libraries
- All components use cn() for class merging

# Commands
- Frontend dev: `cd frontend && npm run dev`
- Backend dev: `cd backend && uvicorn app.main:app --reload`
- Tests: `cd frontend && npx vitest run`
- Lint: `cd frontend && npx biome check --write .`
- Type check: `cd frontend && npx tsc --noEmit`
- E2E: `cd frontend && npx playwright test`

# Workflow
- IMPORTANT: Always run type check + lint after code changes
- Create git branch for each feature (feat/...) or fix (fix/...)
- Use conventional commits: feat:, fix:, refactor:, docs:
- NEVER commit .env files or secrets
- When modifying API endpoints, regenerate the TypeScript client

# Testing
- Write tests for all new features
- Test edge cases: empty inputs, auth failures, Unicode, null values
```

### Recommended Daily Workflow

Use Claude Code's **Plan Mode** (Shift+Tab twice) to separate exploration from execution:
1. Explore codebase → create detailed plan → review plan
2. Switch to Normal Mode → implement → verify

For complex features, use **multi-session development:**
- Session 1: writes the spec and tests
- Session 2: implements constrained by those tests
- Session 3: reviews from fresh context

Key session management:
- Use `/clear` when switching between unrelated tasks
- Manually run `/compact` at ~50% context usage
- Write plans to external files (`plan.md`, `SPEC.md`) for persistence across sessions
- Avoid exceeding 20K tokens of MCP context

### Recommended Project Structure

```
neodemos/
├── CLAUDE.md
├── docker-compose.yml
├── .github/workflows/
│   ├── ci.yml
│   └── deploy.yml
├── frontend/                    # Next.js + Payload CMS
│   ├── src/
│   │   ├── app/                 # App Router pages & layouts
│   │   │   ├── (public)/        # Public-facing routes
│   │   │   ├── (dashboard)/     # Authenticated dashboard
│   │   │   └── (payload)/       # Payload admin panel
│   │   ├── collections/         # Payload CMS collection configs
│   │   ├── blocks/              # Payload page builder blocks
│   │   ├── components/
│   │   │   ├── ui/              # shadcn/ui base components
│   │   │   ├── layout/          # Navbar, footer, sidebar
│   │   │   ├── features/        # Feature-specific components
│   │   │   └── shared/          # Reusable cross-feature components
│   │   ├── lib/                 # API client, utilities
│   │   ├── hooks/               # Custom React hooks
│   │   ├── types/               # TypeScript definitions
│   │   └── styles/              # Global styles, token bridge
│   ├── payload.config.ts        # Payload CMS configuration
│   ├── biome.json               # Linting + formatting
│   ├── vitest.config.ts
│   └── playwright.config.ts
├── backend/                     # FastAPI application
│   ├── app/
│   │   ├── main.py
│   │   ├── api/v1/              # API routes
│   │   ├── models/              # SQLAlchemy models
│   │   ├── services/            # Business logic
│   │   └── core/                # Config, security
│   ├── tests/
│   └── alembic/                 # Database migrations
├── shared/
│   └── openapi.json             # Auto-generated OpenAPI spec
└── scripts/
    └── generate-api-client.sh
```

Use FastAPI's automatic OpenAPI generation plus `@hey-api/openapi-ts` to generate type-safe TypeScript clients automatically.

### Testing and Quality Tooling Stack

| Layer | Tool | Rationale |
|-------|------|-----------|
| Unit/Integration | **Vitest** | 4–20× faster than Jest, native ESM/TS |
| E2E | **Playwright** | Officially recommended by Next.js |
| Component | **React Testing Library** | Behavior-focused testing with Vitest |
| Backend | **pytest + httpx** | FastAPI's excellent test client |
| Linting + Format | **Biome v1.8+** | Single Rust binary replacing ESLint + Prettier, 10–25× faster |
| Pre-commit | **Husky v9 + lint-staged** | Catches issues before they reach the repo |

---

## 3. Part 2: Mac-Based Design Tools

### The Honest Answer

No single Mac desktop app provides a Squarespace-like visual editor that directly edits and deploys a Next.js project. Your visual editing interface is:
- **Payload CMS admin panel** (for content)
- **Claude Code** (for layout and design changes via natural language)
- **Your browser** (for seeing the result)

### Webflow and Framer Are Dead Ends

Webflow exports static HTML/CSS only — no React, no Tailwind. Framer does not support code export or self-hosting. Neither is recommended.

### Figma + Locofy — Best Design-to-Code Pipeline

Figma (free for individual use) combined with Locofy.ai produces the strongest design-to-Next.js workflow with Tailwind CSS output and smart GitHub code merge.

### Builder.io — Best Visual Editing Layer

Builder.io is a visual headless CMS that integrates into existing Next.js codebases. Non-developers can drag-and-drop pre-registered React/shadcn components. Free tier: 10 users, 10K monthly views. Can coexist with Payload CMS but adds complexity.

---

## 4. Part 3: Payload CMS — The Clear Winner

### Why Payload CMS v3

Payload CMS v3 (currently v3.82.1) installs directly into your Next.js app — single deployment, single process, single Docker container. It is MIT-licensed, free for self-hosting, has 30K+ GitHub stars, and supports PostgreSQL natively.

### Comparative CMS Analysis

| CMS | Architecture | Same process as Next.js | Admin UI | License |
|-----|-------------|------------------------|----------|---------|
| **Payload v3** | Embedded in Next.js | ✅ Yes | Excellent | MIT (free) |
| **Strapi v5** | Separate Node.js app | ❌ Separate | Very good | MIT (free) |
| **Directus** | Database wrapper | ❌ Separate | Excellent | BSL 1.1 |
| **Sanity** | Cloud data storage | ❌ Cloud | Excellent | Proprietary |
| **Ghost** | Separate Node.js | ❌ Separate | Excellent editor | MIT (free) |

**Recommendation: Payload CMS v3** — single deployment eliminates an entire deployment surface.

---

## 5. Part 4: Docker Compose Deployment on Hetzner CCX33

### Resource Allocation Plan for 32GB RAM

| Service | Estimated RAM |
|---------|--------------|
| OS + Docker | 1–1.5 GB |
| PostgreSQL 16 + pgvector | 8–12 GB |
| Qdrant | 2–4 GB |
| Next.js + Payload CMS | 1–1.5 GB |
| FastAPI | 512MB–1 GB |
| Caddy | 50–100 MB |
| Authentik (server + worker + Redis) | 1.5–2 GB |
| Uptime Kuma | 50–100 MB |
| Buffer/OS cache | 2–4 GB |
| **Total** | **~17–25 GB (7–15 GB headroom)** |

### Coolify vs Plain Docker Compose

**Coolify v4** (35K+ stars) provides GUI management, auto-deploy on Git push, SSL, monitoring, and backup scheduling. Installation: `curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash`. Adds 1–2 GB RAM overhead.

**Recommendation:** Use Coolify if you want a GUI dashboard and auto-deployments. Use plain Docker Compose + Caddy if you want minimal overhead.

### CI/CD with GitHub Actions

GitHub Actions provides 2,000 free minutes/month on Linux runners. Minimal pipeline: Biome lint → tsc type check → Vitest tests → build → Playwright E2E → deploy via SSH.

### Caddy Configuration

```caddyfile
neodemos.nl {
    reverse_proxy nextjs:3000
    encode gzip
}

api.neodemos.nl {
    reverse_proxy fastapi:8000
    encode gzip
}

auth.neodemos.nl {
    reverse_proxy authentik-server:9000
}

mcp.neodemos.nl {
    reverse_proxy fastapi-mcp:8001
    encode gzip
}

status.neodemos.nl {
    reverse_proxy uptime-kuma:3001
}
```

### Backup Strategy

- **pgBackRest v2.58** for PostgreSQL (weekly full + daily differential)
- **Hetzner Storage Box** (~€3.81/mo for 1TB)
- **Qdrant snapshots** via built-in API
- **Hetzner snapshots** before major deployments (~€0.014/GB/mo)
- **Uptime Kuma** for monitoring

---

## 6. Part 5: NeoDemos-Specific Integrations

### NL Design System — Rotterdam Theme

The Rotterdam Design System (RODS) is at `@gemeente-rotterdam/design-tokens` (alpha status). The hybrid token bridge strategy:

1. Install shadcn/ui as primary component system
2. Install Rotterdam design tokens: `npm install @gemeente-rotterdam/design-tokens`
3. Map NLDS tokens to shadcn/ui variables in `globals.css`:

```css
@import "@gemeente-rotterdam/design-tokens/dist/index.css";

:root {
  --background: var(--rods-color-white, 0 0% 100%);
  --foreground: var(--rods-color-black, 222 47% 11%);
  --primary: var(--rods-color-green, 142 76% 36%);
}
```

4. Use NLDS React components only for government-mandated patterns
5. Use shadcn/ui for everything else

**Critical caveat:** Rotterdam's proprietary fonts/logos/brand colors are restricted to Municipality of Rotterdam projects. Create a NeoDemos-branded theme inspired by civic aesthetics.

### WCAG Accessibility

Dutch law (Tijdelijk Besluit Digitale Toegankelijkheid Overheid) requires WCAG 2.1 Level AA. The European Accessibility Act extends requirements from June 2025. shadcn/ui components (built on Radix UI) handle WAI-ARIA compliance, keyboard navigation, and screen reader support.

---

## 7. Part 6: MCP Authentication Fix

### Root Cause

Claude.ai supports two modes for remote MCP servers: authless or OAuth 2.1. There is no API key or basic auth option. The NeoDemos MCP server returned an authentication error during testing.

### Step-by-Step Fix Using Authentik

**Step 1: Create OAuth2 Provider in Authentik**
- Create a new OAuth2/OIDC provider
- Add redirect URIs: `https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback`
- Enable PKCE (S256)

**Step 2: Implement required endpoints:**

```python
@app.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata():
    return {
        "resource": "https://mcp.neodemos.nl/mcp",
        "authorization_servers": [
            "https://auth.neodemos.nl/application/o/<app-slug>/"
        ],
        "scopes_supported": ["mcp:tools"]
    }
```

**Step 3: Return proper 401 on unauthenticated requests:**

```python
@app.middleware("http")
async def auth_middleware(request, call_next):
    if not request.headers.get("Authorization"):
        return Response(
            status_code=401,
            headers={
                "WWW-Authenticate": 'Bearer realm="mcp", '
                'resource_metadata="https://mcp.neodemos.nl/'
                '.well-known/oauth-protected-resource"'
            }
        )
    # Validate token via Authentik introspection endpoint
```

**Step 4: Add connector in Claude.ai**
- Settings → Connectors → Add custom connector
- URL: `https://mcp.neodemos.nl/mcp`
- Advanced settings → enter Authentik Client ID and Client Secret

**Step 5: Configure CORS:**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)
```

**Alternative:** For development, remove authentication entirely (authless mode for public read-only civic data).

### Debugging Tools

```bash
# Test MCP server
npx @modelcontextprotocol/inspector
# Verify 401 with proper WWW-Authenticate header
curl -v https://mcp.neodemos.nl/mcp
# Verify resource metadata endpoint
curl https://mcp.neodemos.nl/.well-known/oauth-protected-resource
```

---

## 8. Part 7: Payload CMS v3 Migration — Step-by-Step

### Step 1 — Install Packages

```bash
pnpm add payload @payloadcms/next @payloadcms/richtext-lexical @payloadcms/db-postgres sharp graphql
```

All `@payloadcms/*` packages must be the same version.

### Step 2 — Environment Variables

```env
DATABASE_URI="postgres://user:password@localhost:5432/your_database"
PAYLOAD_SECRET="generate-with-openssl-rand-base64-32"
NEXT_PUBLIC_SERVER_URL="https://neodemos.nl"
```

### Step 3 — Create `payload.config.ts`

```typescript
import sharp from 'sharp'
import { lexicalEditor } from '@payloadcms/richtext-lexical'
import { postgresAdapter } from '@payloadcms/db-postgres'
import { buildConfig } from 'payload'
import { Users } from './src/collections/Users'
import { Pages } from './src/collections/Pages'
import { Media } from './src/collections/Media'
import { CouncilDocuments } from './src/collections/CouncilDocuments'
import { Motions } from './src/collections/Motions'
import { Investigations } from './src/collections/Investigations'
import { seoPlugin } from '@payloadcms/plugin-seo'
import { nl } from '@payloadcms/translations/languages/nl'
import { en } from '@payloadcms/translations/languages/en'

export default buildConfig({
  editor: lexicalEditor(),
  collections: [Users, Pages, Media, CouncilDocuments, Motions, Investigations],
  secret: process.env.PAYLOAD_SECRET || '',
  db: postgresAdapter({
    pool: { connectionString: process.env.DATABASE_URI || '' },
  }),
  sharp,
  plugins: [
    seoPlugin({
      collections: ['pages', 'investigations'],
      tabbedUI: true,
      generateTitle: ({ doc }) => `${doc.title} | NeoDemos`,
      generateURL: ({ doc }) => `${process.env.NEXT_PUBLIC_SERVER_URL}/${doc.slug}`,
    }),
  ],
  admin: {
    user: 'users',
    meta: { titleSuffix: '- NeoDemos CMS' },
    livePreview: {
      url: ({ data }) => `${process.env.NEXT_PUBLIC_SERVER_URL}/${data.slug}`,
      collections: ['pages', 'investigations'],
      breakpoints: [
        { label: 'Mobile', width: 375, height: 667 },
        { label: 'Tablet', width: 768, height: 1024 },
        { label: 'Desktop', width: 1440, height: 900 },
      ],
    },
  },
  i18n: {
    supportedLanguages: { en, nl },
    fallbackLanguage: 'nl',
  },
  localization: {
    locales: [
      { label: 'Nederlands', code: 'nl' },
      { label: 'English', code: 'en' },
    ],
    defaultLocale: 'nl',
    fallback: true,
  },
  typescript: {
    outputFile: 'src/payload-types.ts',
  },
})
```

### Step 4 — Add Path Alias to `tsconfig.json`

```json
{
  "compilerOptions": {
    "paths": {
      "@payload-config": ["./payload.config.ts"]
    }
  }
}
```

### Step 5 — Wrap Next.js Config

```javascript
// next.config.mjs
import { withPayload } from '@payloadcms/next/withPayload'

const nextConfig = {
  output: 'standalone', // required for Docker
}

export default withPayload(nextConfig)
```

### Step 6 — Create Route Group Structure

```
app/
├── (payload)/                      ← Payload admin + API
│   ├── admin/
│   │   └── [[...segments]]/
│   │       ├── page.tsx
│   │       └── not-found.tsx
│   ├── api/
│   │   └── [...slug]/
│   │       └── route.ts
│   ├── graphql/
│   │   └── route.ts
│   ├── custom.scss
│   └── layout.tsx
├── (frontend)/                     ← YOUR existing app
│   ├── layout.tsx
│   ├── page.tsx
│   └── ...all existing routes
```

### Step 7 — Start Dev

```bash
pnpm dev
# Navigate to http://localhost:3000/admin to create first admin user
```

### Collection Definitions for Civic Tech

**Users:**
```typescript
export const Users: CollectionConfig = {
  slug: 'users',
  auth: { tokenExpiration: 7200, maxLoginAttempts: 5, lockTime: 600 * 1000 },
  admin: { useAsTitle: 'email', group: 'Admin' },
  fields: [
    {
      name: 'role', type: 'select', required: true, defaultValue: 'editor',
      options: [
        { label: 'Admin', value: 'admin' },
        { label: 'Editor', value: 'editor' },
        { label: 'Council Member', value: 'council_member' },
      ],
    },
    { name: 'displayName', type: 'text' },
  ],
}
```

**Media:**
```typescript
export const Media: CollectionConfig = {
  slug: 'media',
  upload: {
    staticDir: 'public/media',
    mimeTypes: ['image/*', 'application/pdf'],
    imageSizes: [
      { name: 'thumbnail', width: 400, height: 300, crop: 'center' },
      { name: 'card', width: 768, height: 1024, crop: 'center' },
      { name: 'desktop', width: 1920, height: undefined },
    ],
    adminThumbnail: 'thumbnail',
    focalPoint: true,
  },
  fields: [{ name: 'alt', type: 'text', required: true }],
}
```

**Pages (with block-based layout builder):**
```typescript
export const Pages: CollectionConfig = {
  slug: 'pages',
  admin: { useAsTitle: 'title', group: 'Content' },
  versions: { drafts: { autosave: true }, maxPerDoc: 25 },
  fields: [
    { name: 'title', type: 'text', required: true },
    { name: 'slug', type: 'text', required: true, unique: true,
      admin: { position: 'sidebar' } },
    {
      name: 'layout', type: 'blocks',
      blocks: [Hero, Content, CallToAction],
      required: true,
    },
  ],
}
```

**Council Documents:**
```typescript
export const CouncilDocuments: CollectionConfig = {
  slug: 'council-documents',
  admin: { useAsTitle: 'title', group: 'Civic Data' },
  versions: true,
  fields: [
    { name: 'title', type: 'text', required: true },
    {
      name: 'documentType', type: 'select', required: true,
      options: ['minutes', 'agenda', 'resolution', 'ordinance', 'report'],
    },
    { name: 'meetingDate', type: 'date', required: true },
    { name: 'body', type: 'richText' },
    { name: 'relatedMotions', type: 'relationship', relationTo: 'motions', hasMany: true },
    { name: 'attachments', type: 'array', fields: [
        { name: 'file', type: 'upload', relationTo: 'media' },
        { name: 'description', type: 'text' },
    ]},
    { name: 'sourceUrl', type: 'text' },
  ],
}
```

**Motions:**
```typescript
export const Motions: CollectionConfig = {
  slug: 'motions',
  admin: { useAsTitle: 'title', group: 'Civic Data' },
  fields: [
    { name: 'title', type: 'text', required: true },
    { name: 'motionNumber', type: 'text', unique: true },
    {
      name: 'status', type: 'select', required: true,
      options: ['proposed', 'discussion', 'passed', 'rejected', 'tabled', 'withdrawn'],
    },
    { name: 'description', type: 'richText' },
    { name: 'proposedBy', type: 'text' },
    { name: 'dateProposed', type: 'date' },
    { name: 'dateDecided', type: 'date' },
    { name: 'councilDocument', type: 'relationship', relationTo: 'council-documents' },
    {
      name: 'votes', type: 'group',
      fields: [
        { name: 'yea', type: 'number', defaultValue: 0 },
        { name: 'nay', type: 'number', defaultValue: 0 },
        { name: 'abstain', type: 'number', defaultValue: 0 },
      ],
    },
  ],
}
```

**Investigations:**
```typescript
export const Investigations: CollectionConfig = {
  slug: 'investigations',
  admin: { useAsTitle: 'title', group: 'Civic Data' },
  versions: { drafts: true },
  fields: [
    { name: 'title', type: 'text', required: true },
    { name: 'slug', type: 'text', required: true, unique: true },
    { name: 'summary', type: 'textarea' },
    { name: 'content', type: 'richText' },
    {
      name: 'investigationStatus', type: 'select',
      options: ['in_progress', 'published', 'archived'],
      defaultValue: 'in_progress',
    },
    { name: 'author', type: 'relationship', relationTo: 'users' },
    { name: 'relatedMotions', type: 'relationship', relationTo: 'motions', hasMany: true },
    { name: 'relatedDocuments', type: 'relationship', relationTo: 'council-documents', hasMany: true },
    { name: 'publishedDate', type: 'date' },
  ],
}
```

### Block Definitions (Page Builder)

```typescript
// src/blocks/Hero.ts
export const Hero: Block = {
  slug: 'hero',
  labels: { singular: 'Hero Section', plural: 'Hero Sections' },
  fields: [
    { name: 'heading', type: 'text', required: true },
    { name: 'subheading', type: 'text' },
    { name: 'backgroundImage', type: 'upload', relationTo: 'media' },
    { name: 'cta', type: 'group', fields: [
        { name: 'label', type: 'text' },
        { name: 'href', type: 'text' },
    ]},
  ],
}

// src/blocks/Content.ts
export const Content: Block = {
  slug: 'content',
  fields: [
    { name: 'richText', type: 'richText' },
    { name: 'columns', type: 'select',
      options: [
        { label: 'One Column', value: 'one' },
        { label: 'Two Columns', value: 'two' },
      ],
    },
  ],
}

// src/blocks/CallToAction.ts
export const CallToAction: Block = {
  slug: 'cta',
  fields: [
    { name: 'heading', type: 'text', required: true },
    { name: 'description', type: 'textarea' },
    { name: 'buttonText', type: 'text' },
    { name: 'buttonLink', type: 'text' },
    { name: 'style', type: 'select', options: ['primary', 'secondary', 'outline'] },
  ],
}
```

### Frontend Block Rendering with shadcn/ui

```tsx
// app/(frontend)/[slug]/page.tsx
import { getPayload } from 'payload'
import config from '@payload-config'
import { HeroBlock } from '@/components/blocks/HeroBlock'
import { ContentBlock } from '@/components/blocks/ContentBlock'
import { CTABlock } from '@/components/blocks/CTABlock'

const blockComponents = {
  hero: HeroBlock,
  content: ContentBlock,
  cta: CTABlock,
}

export default async function Page({ params }: { params: { slug: string } }) {
  const payload = await getPayload({ config })
  const result = await payload.find({
    collection: 'pages',
    where: { slug: { equals: params.slug } },
  })
  const page = result.docs[0]

  return (
    <>
      {page.layout.map((block, i) => {
        const Component = blockComponents[block.blockType]
        return Component ? <Component key={i} {...block} /> : null
      })}
    </>
  )
}
```

### Hooks for FastAPI Integration

```typescript
hooks: {
  afterChange: [
    async ({ doc, req, context }) => {
      if (context.skipSync) return doc
      await fetch(`${process.env.FASTAPI_URL}/api/index`, {
        method: 'POST',
        body: JSON.stringify({ id: doc.id, content: doc.body }),
        headers: { 'Content-Type': 'application/json' },
      })
      return doc
    },
  ],
},
```

### Docker Setup

**Dockerfile:**
```dockerfile
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production
RUN corepack enable && pnpm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 payload
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
RUN mkdir -p /app/public/media && chown -R payload:nodejs /app/public/media
USER payload
EXPOSE 3000
CMD ["node", "server.js"]
```

### Common Migration Pitfalls

- All `@payloadcms/*` packages must be the exact same version
- Set `PAYLOAD_SKIP_DB_CONNECT=true` for Docker builds without DB access
- `output: 'standalone'` is required in `next.config.mjs` for Docker
- Use the Local API (`payload.find()`) in server components — zero HTTP overhead
- Custom components are Server Components by default in v3; add `'use client'` for interactive ones

---

## 9. Part 8: Mac Desktop App Analysis — Full Comparison

### Comparison Matrix

| Tool | Edits Next.js | Tailwind CSS | Remote server | Visual design | Pricing | Verdict |
|---|---|---|---|---|---|---|
| **Cursor** | ✅ Direct JSX/TSX | ✅ Excellent | ✅ SSH | ❌ Code only | Free–$20/mo | **✅ RECOMMENDED** |
| **VS Code** | ✅ Direct JSX/TSX | ✅ Excellent | ✅ SSH | ❌ Code only | Free | ✅ Free alternative |
| **Payload CMS admin** | N/A (content) | N/A | ✅ Built-in | ✅ Block builder | Free | **✅ RECOMMENDED** |
| **Builder.io** | ❌ External | Indirect | N/A (web) | ✅ Drag-and-drop | Free–$19+ | ⚠️ Adds complexity |
| **Figma** | ❌ Design only | Via plugins | N/A | ✅ Full visual | Free–$15/mo | ⚠️ For mockups |
| **Piny** (VS Code ext) | ✅ JSX/TSX | ✅ Visual controls | ⚠️ Via SSH | ⚠️ Tailwind only | Free–paid | ⚠️ Promising, new |
| **Nova** | ✅ Direct JSX/TSX | ✅ Extension | ✅ Best SFTP | ❌ Code only | $99/yr | ⚠️ Mac-native fans only |
| **Pinegrow desktop** | ❌ HTML only | ✅ Excellent | ❌ None | ✅ Visual HTML | $12/mo | ❌ NO |
| **WebStorm** | ✅ Direct JSX/TSX | ✅ Built-in | ✅ Gateway | ❌ Code only | Free–$149/yr | ❌ Redundant |
| **Sketch** | ❌ Design only | ⚠️ Theme only | ❌ None | ✅ Full visual | $120 | ❌ Figma is better |
| **RapidWeaver** | ❌ Own format | Internal only | ✅ FTP | ✅ Visual | ~$119 | ❌ NO |
| **Blocs** | ❌ Own format | ❌ Bootstrap | ⚠️ Basic | ✅ Visual | $113–180 | ❌ NO |
| **Hype** | ❌ Animations | ❌ None | ❌ None | ✅ Animation | $50–100 | ❌ NO |

---

## 10. Part 9: The Definitive Daily Workflow

### Three Windows Open at All Times

1. **Browser → `https://neodemos.nl/admin`** — Payload CMS admin panel for content editing (pages, posts, council documents, motions, investigations) using the block-based page builder with live preview. No code required.

2. **Browser → `https://neodemos.nl`** — Your live site, showing changes in real time via hot module replacement during development.

3. **Cursor (Mac app, $20/month)** connected via SSH to Hetzner, with **Claude Code running in its integrated terminal**. Describe design changes in natural language: *"Make the hero section background a gradient from blue to purple, increase the heading font size, and add a shadow to the CTA button."* Claude Code edits the Tailwind/shadcn/React files. Browser refreshes automatically.

### When to Use What

| Task | Tool |
|------|------|
| Create/edit a blog post or investigation | Payload admin panel |
| Add a new page with blocks | Payload admin panel |
| Change visual design (colors, layout, spacing) | Claude Code via Cursor terminal |
| Add a new component or feature | Claude Code |
| Quick CSS tweak | Cursor directly |
| Upload images/PDFs | Payload admin panel media library |
| Mockup a new page layout before building | Figma (optional) |

---

## 11. Part 10: MCP Diagnostic Feedback

### NeoDemos MCP Status (April 13, 2026)

- MCP server at `https://mcp.neodemos.nl/mcp` returned authentication error when called from Claude.ai project context
- Tools loaded successfully via `tool_search`: `get_neodemos_context`, `vraag_begrotingsregel`, `analyseer_agendapunt`, `haal_vergadering_op`, `zoek_raadshistorie`
- The auth error blocks actual tool execution
- **Root cause:** Missing OAuth 2.1 infrastructure (see Part 6 for fix)

### Known MCP Tool Issues (from prior sessions)

- `tijdlijn_besluitvorming` — groups results under "0000"; unreliable
- `zoek_moties` — matches too broadly; `uitkomst` field shows "?" despite title-level status
- `zoek_financieel` — surfaces older documents when querying recent data
- `zoek_uitspraken` — disrupted by role changes (raadslid → wethouder)
- Duplicate results across tools consume result slots; no server-side dedup
- No chronological sorting in any tool output
- `lees_fragment` requires document_id but search results may return chunk_id

### Recommendations for Claude Code

1. Fix MCP auth first (OAuth 2.1 via Authentik or switch to authless for development)
2. Add `commissie` filter parameter to tools
3. Implement server-side deduplication
4. Return `source_url` field in all tool responses
5. Fix `uitkomst` field parsing in `zoek_moties`
6. Add chronological sorting option to all tools
7. Standardize on single MCP namespace (not `neodemos` vs `neodemos_v3`)

---

## Appendix A: Cost Summary

### Monthly Operating Costs

| Item | Cost |
|------|------|
| Hetzner CCX33 | ~€49.99 |
| Hetzner Storage Box (1TB backups) | ~€3.81 |
| Hetzner Snapshots (3 × 50GB) | ~€2.15 |
| GitHub Actions | €0 (free tier) |
| Payload CMS | €0 (MIT, self-hosted) |
| Builder.io | €0 (free tier, optional) |
| Coolify | €0 (self-hosted) or $5 (cloud) |
| Cursor Pro | $20/mo (optional, free tier available) |
| Domain + DNS | ~€1–5 |
| **Total** | **~€57–85/month** |

### One-Time Knowledge Graph Build Cost

| Item | Cost |
|------|------|
| LLM extraction (~480K chunks) | ~€150–250 |
| Accuracy audit (Claude Opus) | ~€5 |
| **Total KG build** | **~€155–255** |

---

## Appendix B: Claude Code Instructions for This Project

Copy this as your initial prompt when starting a Claude Code session for NeoDemos frontend work:

```
You are building the frontend for NeoDemos — a Dutch civic intelligence platform
for Rotterdam city council data.

STACK:
- Next.js 15 (App Router) with Payload CMS v3 embedded
- TypeScript strict mode
- Tailwind CSS + shadcn/ui components
- PostgreSQL 16 via @payloadcms/db-postgres adapter
- FastAPI backend at api.neodemos.nl (separate service)
- Deployed via Docker Compose on Hetzner CCX33

RULES:
- Read CLAUDE.md before starting any task
- Use shadcn/ui from components/ui/ — NEVER install alternative UI libraries
- All collections defined in src/collections/
- All page builder blocks in src/blocks/
- Public routes in app/(frontend)/, Payload admin in app/(payload)/
- Use Payload Local API (payload.find()) in server components, NOT REST API
- Dutch is the default language
- Run `pnpm biome check --write .` after all changes
- Run `pnpm tsc --noEmit` to verify types
- Commit with conventional commits: feat:, fix:, refactor:

CURRENT PRIORITIES:
1. Get Payload CMS running with all civic tech collections
2. Build the public search interface (single search box → 3-layer response)
3. Party voting visualization (color-coded per party)
4. Mobile-first responsive design
5. WCAG 2.1 AA compliance
```

---

*Generated from NeoDemos planning session, April 13, 2026.*
*Feed this document to Claude Code as your master specification.*