# MAHAGAMA CONSTITUENCY SETUP — SWARM AGENT SPEC
## Political Edge · Deepika Pandey Singh · JH-AC18
## Full DB population + data source activation

---

## CONTEXT

```
DB:         Connected (Supabase kmrpinbdgvxrpulvjucs — resumed)
Schema:     All tables exist (0001-0014 applied)
Data:       Empty except state_registry (28 rows) + 1 app_user
CSVs:       data/raw/eci/ has 2011, 2014, 2019, 2024 (2011 may be 2009)
Migration:  0015 NOT applied (training_log missing)
Politician: NOT in DB yet
Tenant:     NOT in DB yet
```

---

## SPAWN 5 PARALLEL AGENTS

```
Agent S1: Migration + Politician + Tenant setup    (CRITICAL — others depend on this)
Agent S2: Election data loading                    (depends on S1 politician_id)
Agent S3: Demo data seeding                        (depends on S1 politician_id)
Agent S4: Data source fixes + package installs     (independent)
Agent S5: .env audit + API key placeholders        (independent)
```

S1 must complete first.
S2 + S3 run after S1 signals politician_id.
S4 + S5 run immediately in parallel with S1.

---

## ══════════════════════════════════════════
## AGENT S1: MIGRATION + POLITICIAN + TENANT
## ══════════════════════════════════════════
### RUNS FIRST — blocks S2 and S3

### S1.1 Apply migration 0015
```bash
cd database && alembic upgrade head
# Expected: 0015_training_log applied
# Verify: analytics.training_log + analytics.model_performance exist
```

### S1.2 Create politician row
```python
INSERT INTO political.politicians
  (name, party, state, constituency_name, ac_number, phone, email)
VALUES
  ('Deepika Pandey Singh', 'JMM', 'Jharkhand',
   'Mahagama', 'AC18',
   '+91-9876543210', 'deepika@mahagama-ac18.in')
ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
RETURNING id
→ capture as POLITICIAN_ID
```

### S1.3 Create tenant row
```python
INSERT INTO platform.tenants
  (politician_id, state_code, ac_number,
   constituency_name, ceo_url, app_name, active)
VALUES
  (POLITICIAN_ID, 'JH', 'AC18', 'Mahagama',
   'ceo.jharkhand.gov.in', 'Johar Sathi', TRUE)
ON CONFLICT (politician_id) DO UPDATE SET active = TRUE
RETURNING id
→ capture as TENANT_ID
```

### S1.4 Create sync schedule
```python
INSERT INTO platform.sync_schedule
  (tenant_id, enabled, day_of_month, scope)
VALUES (TENANT_ID, TRUE, 10, 'new_additions')
ON CONFLICT (tenant_id) DO NOTHING
```

### S1.5 Link app_user to politician
```python
UPDATE security.app_users
SET politician_id = POLITICIAN_ID,
    constituency_ids = ARRAY[18]
WHERE email = 'minister@demo.local'
```

### S1.6 Create constituency + blocks + booths
```python
# Insert constituency
INSERT INTO electoral.constituencies
  (ac_number, constituency_name, state, politician_id,
   total_booths, district)
VALUES
  ('AC18', 'Mahagama', 'Jharkhand', POLITICIAN_ID,
   211, 'Godda')
ON CONFLICT (ac_number) DO UPDATE SET politician_id = EXCLUDED.politician_id
RETURNING id AS constituency_id

# Insert 8 blocks
blocks = [
  ('Mahagama East', 1),
  ('Mahagama West', 2),
  ('Rajmahal North', 3),
  ('Pathna Block', 4),
  ('Barhait Sector', 5),
  ('Sundarpahari', 6),
  ('Taljhari', 7),
  ('Boarijore', 8),
]
INSERT INTO administrative.blocks (block_name, block_number, constituency_id)
VALUES ... ON CONFLICT DO NOTHING

# Insert 211 polling booths distributed across 8 blocks
# ~26 booths per block (211 / 8)
for booth_num in range(1, 212):
    block_id = blocks[booth_num % 8]
    INSERT INTO electoral.polling_booths
      (booth_number, booth_name, block_id, constituency_id,
       voters_count)
    VALUES (booth_num, 'Booth {booth_num}', block_id, constituency_id, 900)
    ON CONFLICT (booth_number, constituency_id) DO NOTHING
```

### S1 OUTPUT — write to file for S2/S3
```
After all inserts, write to /tmp/s1_output.json:
{
  "politician_id": "...",
  "tenant_id": "...",
  "constituency_id": "...",
  "status": "complete"
}
```

---

## ══════════════════════════════════════════
## AGENT S2: ELECTION DATA LOADING
## ══════════════════════════════════════════
### Wait for S1 to write /tmp/s1_output.json

### S2.1 Check CSV year labels
```python
# Check what year is actually inside the 2011 CSV
import pandas as pd
df = pd.read_csv('data/raw/eci/*2011*.csv', nrows=5)
print(df['election_year'].unique() if 'election_year' in df.columns else df.head())

# If it's 2009 data:
#   Rename data/raw/eci/*2011* → *2009*
#   Rename data/cleaned/*2011* → *2009*
```

### S2.2 Update ELECTION_YEARS in results_loader.py
```python
# FROM: ELECTION_YEARS = [2004, 2009, 2014, 2019, 2024]
# TO:   Discover from filesystem — don't hardcode
# Use glob to find available cleaned CSVs and extract years

import glob, re
def get_available_years(ac_number: str) -> list[int]:
    pattern = f"data/cleaned/*{ac_number}*clean*.csv"
    files = glob.glob(pattern)
    years = []
    for f in files:
        match = re.search(r'_(\d{4})\.csv', f)
        if match:
            years.append(int(match.group(1)))
    return sorted(years)

# Replace hardcoded ELECTION_YEARS with this function
```

### S2.3 Load all available years into DB
```python
# Read politician_id from /tmp/s1_output.json
# Run ECIResultsLoader.load_all(politician_id, 'AC18')
# This loads all years from cleaned CSVs into booth_election_results

from backend.data_sources.eci.results_loader import ECIResultsLoader
loader = ECIResultsLoader(DATABASE_URL)
await loader.load_all(politician_id, 'AC18')
```

### S2.4 Verify load
```sql
SELECT election_year, COUNT(*) as booths,
       ROUND(AVG(our_vote_share_pct),1) as avg_our_share,
       ROUND(AVG(turnout_pct),1) as avg_turnout,
       SUM(CASE WHEN we_won THEN 1 ELSE 0 END) as booths_won
FROM electoral.booth_election_results
WHERE politician_id = POLITICIAN_ID
GROUP BY election_year
ORDER BY election_year;
```

### S2.5 Scrape missing years via ECI scraper
```python
# Years we have CSVs for: whatever was in cleaned/
# Years we want but don't have: check which are missing from 2005,2009,2014,2019,2024

# For each missing year, run scraper:
# python backend/data_sources/eci/scraper.py 
#   --politician-id POLITICIAN_ID
#   --year MISSING_YEAR
#   --ac-number AC18

# Then run cleaner + loader for that year
```

---

## ══════════════════════════════════════════
## AGENT S3: FULL DEMO DATA SEEDING
## ══════════════════════════════════════════
### Wait for S1 to write /tmp/s1_output.json

```python
# Read politician_id + tenant_id from /tmp/s1_output.json
# Run seed script with correct IDs

python backend/scripts/seed_demo_data.py \
  --politician-id POLITICIAN_ID \
  --tenant-id TENANT_ID

# If seed script doesn't accept args, patch it first:
# Replace hardcoded politician_id references with
# the values from /tmp/s1_output.json
```

Seed script must produce:
```
Phase 1:  politician + tenant          → from S1 (skip if exists)
Phase 2:  8 blocks + 211 booths        → from S1 (skip if exists)
Phase 3:  200 voter records            → electoral_roll_master
Phase 4:  150 phone linkages           → phone_voter_linkage (consent=TRUE)
Phase 5:  6 field workers              → one per active block
Phase 6:  40 grievances                → 12 OPEN, 10 IN_PROGRESS, 15 RESOLVED, 3 CLOSED
Phase 7:  25 voice feedback records    → for RESOLVED grievances
Phase 8:  3 broadcast campaigns        → 1 draft, 2 sent
Phase 9:  8 task assignments           → linked to grievances
Phase 10: booth health scores          → analytics.booth_health for all 211 booths
Phase 11: booth election results       → 211 booths × 2 years (from S2 if not done)
```

---

## ══════════════════════════════════════════
## AGENT S4: DATA SOURCE FIXES + PACKAGES
## ══════════════════════════════════════════
### RUNS IMMEDIATELY — independent of S1

### S4.1 Install missing Python package
```bash
pip install google-cloud-texttospeech==2.16.0 --break-system-packages
# Add to backend/api/requirements.txt
```

### S4.2 Install tweepy for Twitter monitor
```bash
pip install tweepy==4.14.0 --break-system-packages
# Add to backend/api/requirements.txt
```

### S4.3 Fix ECI scraper — add multi-year loop
```python
# File: backend/data_sources/eci/scraper.py
# ADD --years CLI argument:

# Current: --year 2024 (single year)
# New:     --years 2009,2014,2019,2024 (comma-separated)

parser.add_argument('--years', type=str, default=None,
    help='Comma-separated years e.g. 2009,2014,2019,2024')

if args.years:
    years = [int(y) for y in args.years.split(',')]
else:
    years = [args.year]

for year in years:
    await scraper.download_constituency_results(
        politician, year, election_type, ac_number
    )
```

### S4.4 Add missing ECI portal URLs for older years
```python
# File: backend/data_sources/eci/scraper.py
# Current portal registry only has 2011, 2014, 2019, 2022, 2023, 2024
# Add 2009 and 2005:

ECI_PORTALS = {
    2005: 'AEGeneral2005',   # Jharkhand first assembly
    2009: 'AEGeneral2009',   # Jharkhand 2009
    2011: 'AEGeneral2009',   # alias for 2009 data
    2014: 'AEGeneral2014',
    2019: 'AEGeneral2019',
    2024: 'AEGeneral2024',
}
# Verify these portal strings against results.eci.gov.in
```

### S4.5 Fix news scraper selector
```python
# File: backend/data_sources/news/scrapers.py
# PrabhatKhabarScraper — test actual fetch and fix selectors

# Test:
import httpx
from bs4 import BeautifulSoup
response = httpx.get('https://www.prabhatkhabar.com', timeout=10)
soup = BeautifulSoup(response.text, 'html.parser')
# Find actual headline CSS selectors
# Update scraper CSS selectors to match current site structure
```

### S4.6 Fix electoral_roll_master columns
```python
# Add preferred_language column if missing
# (needed by voice feedback language detection)

ALTER TABLE electoral.electoral_roll_master
ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10) DEFAULT 'hi';

# Add to migration or run directly
```

---

## ══════════════════════════════════════════
## AGENT S5: .ENV AUDIT + PLACEHOLDERS
## ══════════════════════════════════════════
### RUNS IMMEDIATELY — independent of S1

### S5.1 Check which keys are missing vs present
```python
# Read .env, check each required key
required_keys = [
    'DATABASE_URL', 'DB_HOST', 'DB_USER', 'DB_PASSWORD',
    'SUPABASE_URL', 'SUPABASE_KEY',
    'JWT_SECRET', 'APP_ENCRYPTION_KEY',
    'AISENSY_API_KEY',
    'SARVAM_API_KEY',
    'BHASHINI_API_KEY',
    'GOOGLE_APPLICATION_CREDENTIALS',
    'R2_ACCESS_KEY', 'R2_SECRET_KEY', 'R2_ENDPOINT',
    'R2_BUCKET', 'R2_PUBLIC_URL_PREFIX',
    'TWITTER_BEARER_TOKEN',
    'YOUTUBE_API_KEY',
]
for key in required_keys:
    val = os.getenv(key)
    status = '✅' if val else '❌ MISSING'
    print(f'{status} {key}')
```

### S5.2 Update .env.example with all missing keys
```
Add clearly grouped sections to .env.example:

# ─── VOICE FEEDBACK LOOP (required for WhatsApp features) ───
AISENSY_API_KEY=           # app.aisensy.com → Settings → API Key
SARVAM_API_KEY=            # dashboard.sarvam.ai → API Keys
BHASHINI_API_KEY=          # bhashini.gov.in → Developer → API Key

# ─── GOOGLE CLOUD (required for Hindi/Bengali TTS) ───
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
# Create at: console.cloud.google.com → IAM → Service Accounts
# Enable: Cloud Text-to-Speech API + Cloud Translation API

# ─── CLOUDFLARE R2 (audio file storage) ───
R2_ACCESS_KEY=             # Cloudflare dashboard → R2 → Manage API tokens
R2_SECRET_KEY=
R2_ENDPOINT=               # https://[account_id].r2.cloudflarestorage.com
R2_BUCKET=political-edge-audio
R2_PUBLIC_URL_PREFIX=      # https://pub-[hash].r2.dev

# ─── SOCIAL MEDIA (optional) ───
TWITTER_BEARER_TOKEN=      # developer.twitter.com → Apps → Keys
YOUTUBE_API_KEY=           # console.cloud.google.com → YouTube Data API v3
```

### S5.3 Create API key setup guide
```
File: docs/API_KEYS_SETUP.md

Step by step guide for each service:
1. AiSensy — how to get API key
2. Sarvam AI — registration + key
3. Bhashini — government portal registration
4. Google Cloud — service account setup
5. Cloudflare R2 — bucket creation + tokens
```

---

## SUCCESS CRITERIA

```
After all 5 agents complete, verify:

DB state:
python -c "
import asyncpg, asyncio, os
from dotenv import load_dotenv
load_dotenv()
async def verify():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    checks = [
        ('political.politicians', 1, 'Deepika Pandey Singh'),
        ('platform.tenants', 1, 'JH-AC18'),
        ('electoral.polling_booths', 211, 'all booths'),
        ('electoral.booth_election_results', '800+', 'multi-year'),
        ('electoral.electoral_roll_master', 200, 'seed voters'),
        ('engagement.grievances', 40, 'seed grievances'),
        ('analytics.training_log', 'exists', 'migration 0015'),
    ]
    for table, expected, label in checks:
        count = await conn.fetchval(f'SELECT COUNT(*) FROM {table}')
        status = '✅' if count > 0 else '❌'
        print(f'{status} {table}: {count} ({label})')
    await conn.close()
asyncio.run(verify())
"

Imports:
python -c "from backend.data_sources.eci.results_loader import ECIResultsLoader; print('✅ results_loader')"
python -c "from backend.agents.intelligence.training_pipeline import ContinuousTrainingPipeline; print('✅ training_pipeline')"
python -c "from backend.voice_feedback.trigger_engine import on_grievance_status_changed; print('✅ trigger_engine')"
python -c "from backend.api.main import app; print('✅', len(app.routes), 'routes')"

Web dashboard:
  uvicorn backend.api.main:app --reload --port 8000
  Open http://localhost:5173
  Login: minister@demo.local / ChangeMe!2026
  → Dashboard should show real data from Deepika Pandey Singh / Mahagama
```

---

## PASTE INTO CLAUDE CODE

```
Read MAHAGAMA_SETUP_SPEC.md completely before starting.

Execute in bypass permissions mode.

IMPORTANT: Run agents in this order:
  1. Start S1 first (politician/tenant setup)
  2. Start S4 + S5 immediately in parallel with S1
  3. Once S1 writes /tmp/s1_output.json → start S2 + S3

Constituency: Deepika Pandey Singh · Mahagama AC-18 · Jharkhand · JMM

Agent S1: Migration 0015 + politician + tenant + constituency + blocks + 211 booths
Agent S2: CSV year fix + election data loading (2009/2014/2019/2024)
Agent S3: Full demo data seed (200 voters, 40 grievances, 6 field workers, 25 feedback)
Agent S4: Package installs + multi-year scraper + news scraper fix
Agent S5: .env audit + .env.example update + API key setup guide

After all complete — run success criteria checks.
Report: DB row counts per table, import checks, route count.
```
