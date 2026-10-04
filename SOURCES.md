# Data source evaluation

Evaluated on 2026-10-03 against Tibia client 15.33.df9fa3.

| Source | Used | Fields it reliably provides | Notes |
| --- | --- | --- | --- |
| Installed Tibia client (`assets/appearances-*.dat`, `package.json`) | **Yes**, primary for IDs | Client type ID, in-game name, market category, stackable, Cyclopedia canonical ID, NPC trade offers (NPC, location, price NPC charges, price NPC pays, currency), client version | Local and authoritative for the client that reads the loot file. Contains about 250 duplicate names, so it is never searched by name alone. Market variants (objects that trade as another ID) are folded into their main item. |
| [TibiaWiki Fandom – Delivery Task](https://tibia.fandom.com/wiki/Delivery_Task) via MediaWiki API | **Yes**, primary for candidates | Item, task category (section), min and max requested quantity, "NPC Buy Price", page revision time | 488 items on 2026-09-04. Tables are parsed by header name, so a missing price column (*Others*) gives an empty value instead of a shifted one. |
| TibiaWiki Fandom item pages via MediaWiki API | **Yes** | Disambiguated page title (e.g. *Bag (Ahmet)*), `itemid` (client IDs), `actualname`, `primarytype`, `droppedby`, `npcvalue` (NPC pays), `npcprice` (NPC charges), page revision time | All pages that use `Template:Infobox Object` are indexed. The list comes in batches of 500, with `lastrevid` per page, and content comes in batches of 50. After the first full fetch (about 150 requests), updates download only pages whose revision changed. A page is attached to a client item only if it is the sole page listing that ID, or the sole one with the same in-game name. |
| [TibiaWiki Fandom – Items](https://tibia.fandom.com/wiki/Items) | No | Category index pages | The client already provides the full catalog with IDs. |
| [TibiaWiki BR – Weekly Tasks](https://www.tibiawiki.com.br/wiki/Weekly_Tasks), [Itens](https://www.tibiawiki.com.br/wiki/Itens) | No | Portuguese description of weekly tasks (revised 2026-08-18) | Useful for checking by hand. Adds nothing over the Fandom data for this purpose. |
| [TibiaWiki API project](https://github.com/benjaminkomen/TibiaWikiApi) / [tibiawiki.dev](https://tibiawiki.dev/) | No | JSON form of the same infobox fields (`itemid`, `droppedby`, `npcvalue`, …) | Unofficial, but responsive and current when checked. Bulk export (`/api/items?expand=true`) is refused above 5,000 pages (the wiki has about 6,560 item pages) and has no paging, so it cannot provide the full index. The wiki's own API is used instead. |
| [TibiaPal Deliveries](https://tibiapal.com/deliveries) | No (reference) | Delivery-item market values | Says it is not maintained for Summer 2026 items and that values vary by world. Not used as a live source. |
| [Official Quick Loot guide](https://www.tibia.com/gameguides/?section=controls&subtopic=manual) | Linked | User workflow (Cyclopedia) | Linked from the Copy & export tab. |
| [PCGamingWiki](https://www.pcgamingwiki.com/wiki/Tibia), [TibiaQA](https://www.tibiaqa.com/3985/how-transfer-edit-characters-loot-list-without-the-need-log-the-character), [TibiaBR forum](https://forums.tibiabr.com/threads/512965-Feedback-Quick-Looting) | Leads only | File location and older JSON examples | The format is checked against the user's own files instead. |

## Source types

Every source and every value is labelled with one type, and the types are never mixed:

| Type | Sources | Treated as |
| --- | --- | --- |
| Official client data | Installed Tibia client | Authoritative for Tibia item IDs, names and NPC offers in that client version |
| Official Tibia website | Quick Loot guide | Linked for instructions; no data is downloaded |
| Community wiki | TibiaWiki pages and the item index | Usually accurate but community-maintained; cross-checked against the client |
| Third-party estimate | (none configured) | Estimates that vary by world; never authoritative |

Each source's last successful update and last error are recorded in `source_log.json` and shown on the Data sources
tab. Each value records its source, type, retrieval time and, for market data, the world.

## Market values

No dependable market source per world was found among the sources above, so the app shows no market values.
To add one, implement `values.MarketValueProvider` (returning `ItemValue(kind=MARKET, world=..., retrieved_at=...)`)
and register it in `values.MARKET_PROVIDERS`.

## Request etiquette

All HTTP requests go through `sources/http.PoliteHttpClient`:

- They are sent only when you click a button.
- Requests are at least 1 second apart.
- The client sends a descriptive User-Agent.
- It retries with backoff on 429/5xx responses and respects `Retry-After`.
