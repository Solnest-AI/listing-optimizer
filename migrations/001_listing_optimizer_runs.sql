-- Listing Optimizer — Supabase Migration v1  (OPTIONAL)
-- Enables cross-device / cross-tool run memory for the Listing Optimizer skill.
-- You only need this if you want your run history in Supabase; the tool always
-- keeps a local history (state/history.jsonl) regardless.
--
-- Run it ONCE in YOUR OWN Supabase project:
--   Supabase Dashboard → SQL Editor → New query → paste this file → Run.
-- (Claude can also apply it for you via the Supabase MCP the first time you run
--  the optimizer with Supabase connected.)
--
-- This table is PRICE-FREE by design — it never stores rates, ADR, revenue, or
-- min-stay. scripts/memory.py scans every record with the same guardrail the
-- report renderer uses and refuses to write if a price slips in.

CREATE TABLE IF NOT EXISTS public.listing_optimizer_runs (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at            timestamptz DEFAULT now(),
  listing_slug          text NOT NULL,
  listing_name          text,
  city                  text,
  run_date              date NOT NULL,
  season                text,
  ale_total             numeric,                      -- mean of the ALE dimension scores
  ale_scores            jsonb DEFAULT '[]'::jsonb,    -- [{"dimension": "...", "score": 0}]
  title                 text,                         -- the optimized title produced this run
  summary_char_count    integer,
  applied               boolean DEFAULT false,        -- was write-back pushed to the PMS this run
  photo_hero            integer,                      -- recommended hero photo (order index)
  photo_top5            jsonb DEFAULT '[]'::jsonb,    -- recommended top-5 order
  reshoot_count         integer,
  amenity_gaps          jsonb DEFAULT '[]'::jsonb,    -- comp amenity gaps surfaced
  funnel                jsonb DEFAULT '{}'::jsonb,    -- price-free funnel subset (rank/views/ctr/booking_rate/occupancy)
  occupancy_forward_pct numeric,
  occupancy_monthly     jsonb DEFAULT '{}'::jsonb,
  cadence_marked        jsonb DEFAULT '[]'::jsonb,    -- cadence items refreshed this run
  result_path           text                          -- pointer to the full local result.json
);

-- One row per listing per run-date; a same-day re-run UPSERTs over the prior one.
CREATE UNIQUE INDEX IF NOT EXISTS listing_optimizer_runs_slug_date_idx
  ON public.listing_optimizer_runs (listing_slug, run_date);

-- Fast "most recent runs for this listing" lookup.
CREATE INDEX IF NOT EXISTS listing_optimizer_runs_slug_recent_idx
  ON public.listing_optimizer_runs (listing_slug, run_date DESC);

-- RLS on (Supabase default). The service role bypasses RLS anyway; this policy
-- just makes the intent explicit. Tighten for a multi-user setup.
ALTER TABLE public.listing_optimizer_runs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'listing_optimizer_runs' AND policyname = 'service_all'
  ) THEN
    CREATE POLICY service_all ON public.listing_optimizer_runs
      FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
