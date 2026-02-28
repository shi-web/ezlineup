"""Calculator tool for fantasy point projections and budget sums."""
from langchain_core.tools import tool


@tool
def calculator_tool(expression: str) -> str:
    """Evaluate a mathematical expression. Use for projecting fantasy points, lineup totals, or budget sums.
    expression: e.g. '45 + 32 + 28' or '12.5 * 1.2'. Only numbers and + - * / ( ) allowed.
    """
    allowed = set("0123456789+-*/(). ")
    if not all(c in allowed for c in expression):
        return "Error: only numbers and + - * / ( ) allowed."
    try:
        result = eval(expression)
        return str(result)
    except Exception as e:
        return f"Error: {e}"


def get_calculator_tool():
    """Return the calculator tool for the agent."""
    return calculator_tool
