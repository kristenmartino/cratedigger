"""Resident Advisor scraper.

RA's reviews live at residentadvisor.net/reviews. Scrape pattern same as
Boomkat: respect robots.txt, real User-Agent, 1 req/sec.

Stub for now — implement at week 2.
"""
from __future__ import annotations

import logging

from agent.sources.rss import RawRelease

logger = logging.getLogger("cratedigger-agent.sources.resident_advisor")


async def scrape_resident_advisor() -> list[RawRelease]:
    """Stub. Implement in week 2."""
    logger.info("Resident Advisor scrape stub — not yet implemented")
    return []
