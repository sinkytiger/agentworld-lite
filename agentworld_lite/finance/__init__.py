"""Finance domain: a multi-agent research desk over public DART disclosures.

The RPG domain's ideas carry over one-to-one:
  items          -> facts (numbers fetched from filings or computed from other facts)
  gathering      -> fetching filings (only the collector can)
  crafting       -> calculating (only the analyst can)
  hand-overs     -> sharing facts with a teammate
  verifier       -> answer checkpoints against ground truth computed from the same filings
  CCE            -> which fetches, calculations and hand-offs ended up in the submitted answer
"""
