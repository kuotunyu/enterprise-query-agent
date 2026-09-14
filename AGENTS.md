# Project working agreement

- Read README.md, the v1 design, acceptance cases and delivery plan first. The 2026-09-14 starting state contains planning documents only.
- Work only within this project's checkout. The upstream Olist project is a read-only source reference; keep its Git checkout, database, containers and volumes intact.
- The owner approved the v1 direction. Follow the current session's stage and resource authorization; plans do not independently authorize paid evaluations, cloud deployment or publication.
- Formal GitHub owner: kuotunyu. Preserve source attribution. Never initialize or publish the whole portfolio root.
- Use separate database bootstrap and SELECT-only runtime credentials. Never give the model shell access, bootstrap credentials or a general-purpose database administration tool.
- docs/acceptance-v1.md contains development cases, not unseen evaluation data. Keep gold answers outside model prompts and preserve failed outcomes.
- Do not silently broaden v1 to another database, more models, fine-tuning, multi-agent orchestration or a benchmark leaderboard.
- Completed milestones stay completed. Fix a demonstrated contract violation; treat a new capability or new study as a separate scope decision.
- Keep secrets, raw data, local paths, provider traces requiring redaction and local handoff records out of public commits. Before publication inspect tracked content and attribution.
