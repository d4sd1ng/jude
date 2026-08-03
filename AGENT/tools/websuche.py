import requests

from core.tool_registry import Tool, ToolRegistry


def web_search(query: str) -> str:
    response = requests.get("https://api.duckduckgo.com/", params={
        "q": query, "format": "json", "no_html": 1, "skip_disambig": 1,
    }, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("AbstractText"):
        return f"{data['AbstractText']}\nQuelle: {data.get('AbstractURL', '')}"
    topics = [item for item in data.get("RelatedTopics", []) if isinstance(item, dict) and item.get("Text")]
    if topics:
        return "\n".join(f"- {item['Text']} ({item.get('FirstURL', '')})" for item in topics[:5])
    return "Keine Sofortantwort gefunden."


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="web_search", description="Sucht eine öffentliche Web-Sofortantwort.", func=web_search,
        param_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ))
