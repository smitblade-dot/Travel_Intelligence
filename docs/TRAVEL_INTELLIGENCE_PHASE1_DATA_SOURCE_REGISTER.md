# Travel Intelligence — Phase 1 Free/Open Data Source Register

**Status:** Working technical register  
**Scope:** 52-country Travel Intelligence foundation  
**Purpose:** Identify data that can be used now, at zero data-licensing cost where possible, before purchasing premium feeds.

## Operating principle

Phase 1 uses free/open sources for development and the initial public intelligence service. Every source must be checked for access method, licence/terms, automation permission, commercial-use restrictions, attribution requirements, update frequency, geographic coverage and reliability.

**Publicly accessible does not automatically mean freely reusable.**

## Priority scale

- **P0 — Build first:** core foundation or high-value live signal
- **P1 — Build next:** important supporting source
- **P2 — Investigate:** useful, but access/rights/technical work needs confirmation
- **P3 — Later:** useful once the core platform is proven

## Phase 1 register

| Category | Source | Access | Cost now | Rights position | Update | Coverage | TI use | Priority |
|---|---|---|---|---|---|---|---|---|
| Geography | OpenStreetMap | Download/API | Free/open data | ODbL; commercial use permitted with licence obligations | Varies | Global | Roads, rail, borders, airports, ports, infrastructure | P0 |
| Geography | Natural Earth | Download | Free | Public domain | Update cycle | Global | Clean map layers, countries, boundaries, roads, rail, airports, ports, rivers | P0 |
| Airports | OurAirports | CSV download | Free | Public domain | Nightly | Global | Airport master database, IATA/ICAO, coordinates, runways | P0 |
| Aircraft | ADSB.lol | API | Free/open | ODbL 1.0 | Near-live | Network-dependent/global | Aircraft positions, movements, diversions/turnbacks signals | P0 |
| Aircraft | OpenSky Network | API | Not suitable for production free use | Commercial/live operational use requires written licence | Near-live | Global/network-dependent | Secondary aviation signal during evaluation only | P2 |
| Weather | Open-Meteo | API | Free for non-commercial evaluation | CC BY 4.0 data; commercial API licence available | Hourly/model dependent | Global | Forecasts, wind, rain, temperature, marine/flood inputs | P0 |
| Disasters | GDACS | API | Free | Public API; verify downstream publication rights | Near-real-time | Global | Earthquakes, floods, cyclones and disaster alerts | P0 |
| Fires | NASA FIRMS | API/WMS/WFS | Free MAP_KEY | Follow applicable NASA data/attribution terms | About 15 min for map services | Global | Wildfires/hotspots near airports, roads, destinations, infrastructure | P0 |
| News/events | GDELT | APIs/data | Free | Unlimited/unrestricted academic, commercial and government use; citation required | Near-real-time/live APIs | Global | Event discovery, news monitoring, geolocation, early warning | P0 |
| Humanitarian | ReliefWeb | API | No fee | API accessible; underlying reports may remain copyrighted by original sources | Real-time | Global | Disaster/conflict/humanitarian reporting and corroboration | P0 |
| Aviation | National AIS / CAA NOTAM sources | Web/feed varies | Usually public access; case-by-case | Assess each authority's terms | Operational | Country/region | NOTAMs, temporary airspace restrictions, military exercises, airport restrictions | P0 |
| Aviation | EUROCONTROL EAD Basic | Registration | Free Basic access | Basic is not operational; operational EAD requires appropriate access | Varies | European | NOTAM/PIB investigation and European aviation data | P2 |
| Airports | Official airport websites | Web/RSS/API where offered | Usually free access | Assess terms/automation rights | Often live | Country-specific | Arrivals, departures, cancellations, airport notices | P0 |
| Airlines | Official airline status pages | Web/API where offered | Usually free access | Assess terms/automation rights | Often live | Route-specific | Flight status, cancellations, route suspensions | P0 |
| Borders | Official border/immigration/customs authorities | Web/notices | Usually free | Assess publication/reuse terms | Event-driven | Country-specific | Border opening/closure, nationality restrictions, crossing rules | P0 |
| Roads | National road authorities | Web/open feeds where offered | Usually free | Case-by-case | Event-driven/live | Country-specific | Closures, incidents, route restrictions | P0 |
| Rail | National railway/operator sites | Web/API/feed where offered | Usually free | Case-by-case | Live/event-driven | Country-specific | Rail disruption, closures, strikes | P1 |
| Ports | Port authorities | Web/notices/API where offered | Usually free | Case-by-case | Event-driven | Country-specific | Port closure/operation, ferry access, disruption | P1 |
| Maritime | Public AIS feeds / authority data | Varies | Free sources exist | Assess each feed's licence | Near-live where available | Variable | Vessel/port movement signals | P2 |
| UAS / drones | National CAA/UAS authorities | Web/notices | Usually free | Case-by-case | Event-driven | Country-specific | Drone restrictions, permits, no-fly zones, temporary restrictions | P0 |
| Aviation | AIP / AIP Supplements | Official AIS sources | Usually public | Case-by-case | Scheduled/event-driven | Country-specific | Permanent and temporary aviation restrictions | P1 |
| Security | Government travel/security notices | Web/feed | Free | Public information; reuse rules vary | Event-driven | Country-specific | Official warnings, restrictions, security events | P0 |
| Security | Police/emergency authorities | Web/social/feed | Free access where public | Case-by-case | Often immediate | Country-specific | Incidents, cordons, public safety restrictions | P1 |
| Local news | Public local media | Web/RSS | Free access | Copyright remains with publisher; use for discovery/linking rather than copying | Often rapid | Country-specific | Early warning and local context | P0 |
| Social OSINT | Public X/Telegram/local OSINT | Public access | Varies | Platform terms and source rights apply; early warning only | Potentially immediate | Global | Early warning and lead generation | P1 |
| Satellite | NASA/USGS open imagery/data | APIs/downloads | Free/open datasets | Dataset-specific | Varies | Global | Fire, flood, environmental change and situational confirmation | P1 |
| Flood | National hydrology/environment agencies | Web/API where offered | Often free | Case-by-case | Near-real-time/event-driven | Country-specific | Flood levels/warnings affecting routes/destinations | P1 |
| Earth observation | Copernicus Sentinel/open datasets | APIs/downloads | Free/open | Dataset-specific EU terms | Varies | Global | Flood/fire/change detection, mapping | P1 |
| Routing | Public OSM-derived routing services | Varies | Some free tiers | Service-specific | Varies | Variable | Route calculation prototype | P1 |
| Traffic | Waze | Investigate | No free production feed confirmed | Commercial/API access must be confirmed | Live | Country-specific | Road closures/incidents/traffic | P2 |
| Mapping/routing | Google Maps/Routes | Commercial API | Paid | Licensed | Live | Global | High-quality routing/traffic later | P3 |
| Flight tracking | Flightradar24 | Commercial/API | Paid | Licensed | Live | Global | High-quality flight tracking later | P3 |
| Maritime intelligence | Kpler | Commercial | Paid | Licensed | Near-live | Global | Vessels, ports, energy/shipping later | P3 |
| Security intelligence | Crisis24/GardaWorld | Commercial | Paid | Licensed | Near-live | Global | Specialist security intelligence later | P3 |

## Initial build set

1. OurAirports — canonical airport reference table.
2. Natural Earth — country/boundary/map reference.
3. OpenStreetMap — detailed geographic/infrastructure layer.
4. GDELT — global news/event discovery.
5. GDACS — disaster alerts.
6. NASA FIRMS — active fire/hotspot detection.
7. ReliefWeb — humanitarian/conflict/disaster reporting.
8. ADSB.lol — experimental live aircraft layer.
9. Official airport sources — live airport status for priority airports.
10. Official airline sources — flight disruption verification.
11. Official border authorities — border status and entry restrictions.
12. Official CAA/AIS/NOTAM sources — airspace and drone restrictions.
13. Official road/rail/port sources — transport disruption.

## Source handling rules

### Discovery is not verification

A source can create a lead. It does not automatically create a published TI event.

Example:

GDELT detects reports of airport closure → investigate original reports → check airport/CAA/airline source → create SourceObservation(s) → assess event status → publish only when evidence supports publication.

### Source quality

Use the existing TI source-quality system:

- **A** — authoritative official/international/directly affected party
- **B** — established professional media
- **C** — recognised specialist/trade/professional body
- **D** — identifiable local/professional OSINT
- **E** — social/unverified
- **F** — unknown/anonymous

### Rights

Do not copy or republish source text simply because it is publicly visible.

Prefer structured data, factual summaries, links to original sources, our own analysis and source attribution.

Record contentRights, accessMethod and automationPermission where known.

## Event model examples

- Airport operating status changed
- Temporary airspace restriction / closure
- UAS operations prohibited or restricted
- Border crossing closed
- Major route blocked
- Rail service suspended
- Port/ferry operations disrupted
- Flight cancelled/diverted/turned back
- Security incident affecting travel
- Flood/fire/earthquake/cyclone affecting travel infrastructure
- Composite event combining multiple observations into a traveller-impact assessment

Example:

Iraq airspace closed + Basra flights disrupted + Kuwait border open + Kuwait airport operating

→ **Southern Iraq: Kuwait may remain a viable exit route, subject to nationality, immigration and security constraints.**

The assessment must clearly distinguish confirmed facts from inference.

## Phase 1 success criteria

The free-data foundation is successful when TI can:

- identify a material event;
- locate it geographically;
- timestamp it;
- preserve the original source;
- distinguish discovery from verification;
- identify affected transport/infrastructure;
- associate the event with one or more of the 52 countries;
- assess traveller impact;
- publish a concise current-intelligence update;
- preserve observation history;
- avoid presenting unverified information as fact.

## Premium-data trigger

Do not buy a commercial feed merely because it is technically attractive.

A paid feed should be added only when:

1. the free source has a demonstrated product gap;
2. the gap materially affects TI usefulness;
3. audience/revenue exists to justify the cost;
4. the licence permits the intended use;
5. the feed can integrate into the existing event/observation model.

**Principle: prove demand with free data; use revenue to buy better data; use better data to create better intelligence.**

## Verified reference sources

- OurAirports: https://ourairports.com/data/
- OpenStreetMap ODbL: https://wiki.openstreetmap.org/wiki/Open_Database_License/ODbL-1.0
- Natural Earth: https://www.naturalearthdata.com/
- ADSB.lol API: https://www.adsb.lol/docs/open-data/api/
- Open-Meteo: https://open-meteo.com/
- GDACS API: https://www.gdacs.org/gdacsapi/swagger/index.html
- NASA FIRMS API: https://firms.modaps.eosdis.nasa.gov/api/
- GDELT: https://gdeltproject.org/
- ReliefWeb API: https://apidoc.reliefweb.int/
- EUROCONTROL EAD Basic: https://www.ead.eurocontrol.int/cms-eadbasic/opencms/en/ead-solutions/ead-basic/
