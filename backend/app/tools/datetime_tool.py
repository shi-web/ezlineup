"""Date/time tool for game times and lineup lock checks."""
from datetime import datetime, timezone
from langchain_core.tools import tool


@tool
def current_datetime_tool(timezone_name: str = "UTC") -> str:
    """Get the current date and time. Use to check game start times, lineup lock, or if information is current.
    timezone_name: Optional; e.g. 'America/New_York'. Defaults to UTC.
    """
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    return f"Current date and time: {now.isoformat()} ({timezone_name})"
