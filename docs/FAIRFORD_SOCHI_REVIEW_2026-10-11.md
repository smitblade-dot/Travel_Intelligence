# Fairford and Sochi coverage repair

Based on main b33c151cbe27eb4d6be51b18a598510882feaccc, whose dataset was generated 11 October (22 events), rather than the older 8 October snapshot.

Two discrete events, five checked source observations, conservative reporting-chain groups. Both are HOT_TOPIC: Fairford is seven days old and remains an investigation/redeployment issue; Sochi is five days old with an unresolved maritime/environmental aftermath. Neither is a fresh alert. CONFIRMED applies to the redeployment and fire/rescue respectively, not responsibility or attack details.

20 October publication-control correction: both restored events remain DRAFT, matching comparable production records with sensitiveAllegation=true and legalReviewStatus=REVIEW_RECOMMENDED. No approval or policy exception is asserted. Their confirmed core developments remain distinct from UNVERIFIED allegations and DISPUTED environmental extent in claimAssessments and uncertainty text. A documented publication decision is outstanding; the public interface continues to show only PUBLISHED events.

Fairford: accessible Reuters copy in Defense News confirms the Pentagon statement. Supplied Air & Space Forces URL was blocked. Guardian reporting and later Times coverage were consulted; no threat mechanism or perpetrator is asserted.

Sochi: Executive Ship Management's 7 October statement independently confirms the fire, rescue and extinguishment. Maritime Executive provides separate imagery/heat-signature reporting. Cyprus Mail reports the Russian authorities' response and restrictions. Moscow Times' 9 October follow-up reports beaches reopening on 8 October and Greenpeace pollution claims conflicting with official tests. Its old 8 October URL redirects to the updated 9 October report, so it is not counted twice. Current pollution extent remains unresolved; no wider port closure or energy-volume loss is inferred.

Collection gaps established in code: closure-only GDELT query; no Russia/Sochi/Fairford aliases; missing maritime/redeployment keywords; geographically unresolved leads discarded. Broadening query coverage, adding place aliases and preserving unresolved leads addresses those gaps. It cannot guarantee recall: the discovery API still caps results at 50 and original-source verification remains mandatory. No run-level evidence establishes which individual filter caused each historical omission.

The public homepage now has a separate Continuing Hot Topics section for published HOT_TOPIC events. Only PUBLISHED records appear publicly. Country event views retain published event coverage. No baseline, upstream oil/gas summary or sibling repository is changed.

Daily prompt now explicitly reviews these subjects and leaves pushing until after existing validation steps. No publication safeguard is bypassed.

20 October integration supersedes the historical baseline above: reconciled against main 05088d3 after Gas/LNG #1 and TI #17 merged. All production records are retained; only the two draft events and their five sources/five observations are added. Dataset generation time and publication counts are regenerated without changing evidence observation dates. Production feed retry/schema handling, 24-hour discovery window and feed-health audit remain intact.
