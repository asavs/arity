"""The runner half of the search tool. The loop calls run(**arguments)."""


def run(**arguments: any) -> str:
    query = arguments.get("query", "")
    return f"executed search with query='{query}'"
