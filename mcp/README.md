# Intervals.icu MCP Server

This directory contains the FastMCP stdio server connecting the `rowing_coach` agent to Intervals.icu.

### Features
- Read athlete wellness & fitness metrics (CTL, ATL, TSB, resting HR, ramp rate).
- Schedule/create structured workouts on the calendar.
- Includes `DRY_RUN=true` mode by default for testing without live API keys.
- Gated behind `require_confirmation=True` in ADK so any calendar write requires human review.

### Environment Variables
- `INTERVALS_API_KEY`: Your personal API key from Intervals.icu Settings -> Developer settings.
- `INTERVALS_ATHLETE_ID`: Your athlete ID (or `0` for self).
- `DRY_RUN`: Set to `"true"` (default) to log payloads and simulate responses without calling the API, or `"false"` to write to your live calendar.
