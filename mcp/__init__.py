"""
MCP layer for BizOps Agent.
Seamlessly integrates with the official 'mcp' SDK while providing
application-specific routing and client management.
"""
import sys
import os

# Temporarily remove current working directory and project root from sys.path
_removed = []
while "" in sys.path:
    sys.path.remove("")
    _removed.append("")

_cur_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_cur_dir)
while _parent_dir in sys.path:
    sys.path.remove(_parent_dir)
    _removed.append(_parent_dir)

try:
    # Pop partially initialized local mcp from sys.modules so official SDK loads
    _partial = sys.modules.pop("mcp", None)
    import mcp as _sdk_mcp
    
    # Extend official mcp.__path__ with this local folder so mcp.router can be resolved
    if _cur_dir not in _sdk_mcp.__path__:
        _sdk_mcp.__path__.append(_cur_dir)
finally:
    for p in reversed(_removed):
        sys.path.insert(0, p)

# Expose official SDK symbols
ClientSession = getattr(_sdk_mcp, "ClientSession", None)
StdioServerParameters = getattr(_sdk_mcp, "StdioServerParameters", None)

# Re-export application router and registry
from mcp.router import MCPRouterManager, ToolRegistry
