# Staged agency-group recheck

Canada corrections PASS. In LAWS_CULTURE_03 one observation and LAWS_CULTURE_07 two observations changed only independenceGroupId from ca-gac to canada-global-affairs. Reversing those three fields reproduces the prior independently accepted hashes exactly. Claims and source evidence are unchanged.

Accepted replacement hashes:
- LAWS_CULTURE_03: `7eded3a52428dd70561edff3002afc16e3cc9d3cabcd6b9b167879d905bf2519`
- LAWS_CULTURE_07: `5f2bac272ce6b0186253b15e736757f4ca443fc30cb38b23cfb4311978e1a87e`

A comprehensive scan of uppercase named candidate batch files across baseline, language and insurance found 66 travel.gc.ca observations; all now use canada-global-affairs. Earlier canada-group-recheck.md was limited to finance and transport through batch 06. Its unqualified closing statement about independence aliases should not be read as a comprehensive audit; this report supersedes that implication.

The same scan found three remaining cross-batch authority aliases, sent to producers for normalization: Jordan Tourism Board jo-jtb / jo-tourism-board, Turespaña es-tourism / es-turespana, and VisitBritain uk-tourism / uk-visitbritain. These are metadata corrections, not new independent corroboration. No final comprehensive grouping PASS is claimed until corrected. This is scoped staging acceptance; production and full launch gates remain unchanged.

## Follow-up normalization PASS

All three remaining authority aliases normalized. Reversing only the independenceGroupId fields reproduces previously accepted hashes exactly. Repeated same-organisation scan over all named staged baseline/language/insurance batches found no remaining multiple group IDs. This metadata scan does not replace claim QA for still-pending batches.

Replacement accepted hashes:
- TRANSPORT_12: `ef9bc23cd131fb11a948970f9f13e73106e0f4b8bffef87ec41caedc58e24096`
- FINANCE_07: `9bf454cf7c26b33cf31e207cf0280f6537e536eb67b575eae384afd08a515837`
